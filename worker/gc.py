#!/usr/bin/env python3
"""Conservative garbage collector for completed, unchanged OHO Git worktrees.

Default behavior is dry-run. A worktree is removable only if it is old enough,
has an OHO task lock marker that can be locked non-blockingly, is registered in
Git's worktree list, has the exact OHO-generated branch pattern, has no tracked,
untracked or ignored changes, and its branch is already an ancestor of the
project root HEAD. Legacy worktrees without lock markers are deliberately kept.
"""
import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

TASK_ID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
BRANCH_RE = re.compile(r"^refs/heads/oho/[0-9a-fA-F]{8}-[0-9a-fA-F]{8}$")


def git(args, cwd=None):
    result = subprocess.run(
        ["git", *(str(a) for a in args)],
        cwd=str(cwd) if cwd else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=30,
    )
    return result


def worktree_records(root: Path):
    result = git(["-C", root, "worktree", "list", "--porcelain"])
    if result.returncode:
        raise RuntimeError("Cannot read Git worktree list: " + result.stderr[-300:])
    records = {}
    current = {}
    for line in result.stdout.splitlines() + [""]:
        if not line:
            if current.get("worktree"):
                records[Path(current["worktree"]).resolve()] = current
            current = {}
            continue
        key, _, value = line.partition(" ")
        if key in {"worktree", "branch", "HEAD", "prunable", "detached", "locked"}:
            current[key] = value or True
    return records


def inspect_candidate(root: Path, path: Path, min_age_hours: float, apply: bool = False):
    """Return (status, detail); only mutate when apply=True."""
    if path.is_symlink() or not path.is_dir():
        return "skip", "not a real directory"
    if not TASK_ID_RE.fullmatch(path.name):
        return "skip", "directory name is not a canonical task UUID"

    base = root.parent / ".oho-worktrees" / root.name
    try:
        base_resolved = base.resolve(strict=True)
        path_resolved = path.resolve(strict=True)
    except OSError:
        return "skip", "worktree path disappeared"
    if path_resolved.parent != base_resolved:
        return "skip", "worktree is outside the expected OHO directory"

    age_hours = (time.time() - path.stat().st_mtime) / 3600
    if age_hours < min_age_hours:
        return "skip", f"younger than minimum age ({age_hours:.1f}h < {min_age_hours:g}h)"

    lock_dir = base / ".locks"
    lock_path = lock_dir / f"{path.name}.lock"
    if lock_dir.is_symlink() or lock_path.is_symlink() or not lock_path.is_file():
        return "skip", "no trusted task lock marker (legacy worktree; manual review required)"

    try:
        fd = os.open(str(lock_path), os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return "skip", "cannot safely open task lock marker"
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "skip", "task lock is held; worker may still be using this worktree"

        if not path.exists() or path.is_symlink():
            return "skip", "worktree disappeared or became a symlink"

        try:
            records = worktree_records(root)
        except RuntimeError as ex:
            return "skip", str(ex)
        record = records.get(path.resolve())
        if not record:
            return "skip", "not registered in git worktree list"
        branch_ref = record.get("branch", "")
        if not isinstance(branch_ref, str) or not BRANCH_RE.fullmatch(branch_ref):
            return "skip", "branch does not match the OHO-generated task pattern"

        other = [p for p, rec in records.items() if p != path.resolve() and rec.get("branch") == branch_ref]
        if other:
            return "skip", "branch is checked out in another worktree"

        status = git(["-C", path, "status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching"])
        if status.returncode:
            return "skip", "cannot inspect Git status: " + status.stderr[-200:]
        if status.stdout.strip():
            return "skip", "worktree contains tracked, untracked or ignored changes"

        ancestor = git(["-C", root, "merge-base", "--is-ancestor", branch_ref, "HEAD"])
        if ancestor.returncode != 0:
            return "skip", "branch contains commits not merged into the project root; preserve for review"

        branch_name = branch_ref.removeprefix("refs/heads/")
        if not apply:
            return "would-remove", f"clean, unlocked, merged worktree; branch {branch_name}"

        remove = git(["-C", root, "worktree", "remove", "--", path])
        if remove.returncode:
            return "skip", "git worktree remove refused: " + remove.stderr[-300:]

        delete_branch = git(["-C", root, "branch", "-d", branch_name])
        if delete_branch.returncode:
            return "removed-worktree-branch-retained", (
                f"worktree removed but branch retained for manual review: {branch_name}; "
                + delete_branch.stderr[-200:]
            )
        return "removed", f"removed worktree and merged branch {branch_name}"
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        # A lock marker is removed only when its corresponding worktree and
        # branch were both successfully removed; the caller handles this below.


def configured_roots(config_path: Path):
    cfg = json.loads(config_path.read_text())
    roots = []
    for raw_path in (cfg.get("project_roots") or {}).values():
        root = Path(raw_path).expanduser().resolve(strict=True)
        if not root.is_dir() or not (root / ".git").exists():
            print(f"SKIP root {root}: not an existing Git checkout", file=sys.stderr)
            continue
        roots.append(root)
    if not roots:
        raise RuntimeError("No valid project_roots found in worker config")
    return roots


def scan(roots, min_age_hours, apply):
    results = []
    for root in roots:
        base = root.parent / ".oho-worktrees" / root.name
        if base.is_symlink() or not base.is_dir():
            continue
        for path in sorted(base.iterdir()):
            if path.name == ".locks":
                continue
            if not path.is_dir() or path.is_symlink():
                continue
            status, detail = inspect_candidate(root, path, min_age_hours, apply=apply)
            results.append((path, status, detail))
    return results


def main():
    parser = argparse.ArgumentParser(description="Safely inspect and clean stale OHO worktrees.")
    parser.add_argument("--config", default=str(Path.home() / ".config/oho/config.json"))
    parser.add_argument("--min-age-hours", type=float, default=24.0,
                        help="minimum worktree directory age (default: 24 hours)")
    parser.add_argument("--apply", action="store_true", help="actually remove eligible worktrees and merged OHO branches")
    parser.add_argument("--yes", action="store_true", help="skip confirmation (requires --apply)")
    args = parser.parse_args()
    if args.min_age_hours < 0:
        parser.error("--min-age-hours must be >= 0")
    if args.yes and not args.apply:
        parser.error("--yes requires --apply")

    try:
        roots = configured_roots(Path(args.config).expanduser())
        if args.apply and not args.yes:
            try:
                answer = input("This will remove only eligible clean, merged OHO task worktrees. Type DELETE to continue: ")
            except EOFError:
                print("Aborted: no interactive confirmation.", file=sys.stderr)
                return 2
            if answer != "DELETE":
                print("Aborted.")
                return 2

        results = scan(roots, args.min_age_hours, apply=args.apply)
        removed = 0
        eligible = 0
        for path, status, detail in results:
            print(f"{status.upper():28} {path} — {detail}")
            if status == "removed":
                removed += 1
            elif status == "would-remove":
                eligible += 1
            if status == "removed":
                # Remove the marker only after the worktree and branch are gone.
                root = next((r for r in roots if path.parent == r.parent / ".oho-worktrees" / r.name), None)
                if root:
                    marker = (root.parent / ".oho-worktrees" / root.name / ".locks" / f"{path.name}.lock")
                    try:
                        marker.unlink(missing_ok=True)
                    except OSError:
                        print(f"NOTICE: could not remove lock marker {marker}", file=sys.stderr)
        if not results:
            print("No OHO task worktrees found.")
        print(f"Summary: {eligible} eligible in dry-run; {removed} removed.")
        if not args.apply:
            print("Dry-run only. Review the list, then rerun with --apply to confirm removals.")
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as ex:
        print(f"oho-gc error: {ex}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
