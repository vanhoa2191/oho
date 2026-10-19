import importlib.util
from pathlib import Path
import pytest

module = Path(__file__).resolve().parents[2] / "worker" / "agent.py"
spec = importlib.util.spec_from_file_location("worker_agent",module)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

def test_command_allowlist():
    config = {"allowed_commands": ["git", "pytest", "echo"]}
    assert agent.command_for({"kind":"command","argv":["pytest","-q"]},config)==["pytest","-q"]
    with pytest.raises(agent.WorkerError):
        agent.command_for({"kind":"command","argv":["git","push"]},config)
    with pytest.raises(agent.WorkerError):
        agent.command_for({"kind":"command","argv":["bash","-c","rm -rf /"]},config)
    with pytest.raises(agent.WorkerError):
        agent.command_for({"kind":"command","argv":["python3","script.py"]},config)

def test_agent_requires_review():
    config = {"agent_binaries":{"codex":"codex"}}
    with pytest.raises(agent.WorkerError):
        agent.command_for({"kind":"agent","model":"codex","prompt":"Build", "approved":False},config)
    argv=agent.command_for({"kind":"agent","model":"codex","prompt":"Build", "approved":True},config)
    assert argv[:2]==["codex","exec"]
    assert "--sandbox" in argv

def test_repo_validation():
    assert agent.validate_repo("https://github.com/a/b") == "https://github.com/a/b"
    with pytest.raises(agent.WorkerError):
        agent.validate_repo("http://evil.example/a/b")

def test_git_command_injection_blocked():
    cfg={"allowed_commands": ["git"]}
    for argv in (["git", "diff", "--ext-diff"], ["git", "show", "--format=%x00"],
                 ["git", "status", "--porcelain", "--untracked-files=all"],
                 ["git", "remote", "update"]):
        with pytest.raises(agent.WorkerError):
            agent.command_for({"kind":"command", "argv":argv}, cfg)
    assert agent.command_for({"kind":"command", "argv":["git", "status", "--short"]}, cfg) == ["git", "status", "--short"]

def test_repo_url_rejects_userinfo():
    with pytest.raises(agent.WorkerError):
        agent.validate_repo("https://user@github.com/a/b")


def test_cloudflare_access_headers(monkeypatch):
    monkeypatch.delenv("CF_ACCESS_CLIENT_ID", raising=False)
    monkeypatch.delenv("CF_ACCESS_CLIENT_SECRET", raising=False)
    assert agent.request_headers({"X-Node-Id": "node"}) == {"X-Node-Id": "node"}
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "client-id")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "client-secret")
    headers = agent.request_headers({"X-Node-Id": "node"})
    assert headers["CF-Access-Client-Id"] == "client-id"
    assert headers["CF-Access-Client-Secret"] == "client-secret"
    monkeypatch.delenv("CF_ACCESS_CLIENT_SECRET")
    with pytest.raises(agent.WorkerError):
        agent.request_headers({})

import os
import subprocess
import uuid

gc_module_path = Path(__file__).resolve().parents[2] / "worker" / "gc.py"
gc_spec = importlib.util.spec_from_file_location("worker_gc", gc_module_path)
gc = importlib.util.module_from_spec(gc_spec)
gc_spec.loader.exec_module(gc)


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)


def _repo_with_worktree(tmp_path):
    root = tmp_path / "oho"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "OHO test")
    _git(root, "config", "user.email", "oho-test@example.invalid")
    (root / "README.md").write_text("test repo\n")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "initial")
    job = {"id": str(uuid.uuid4()), "run_id": str(uuid.uuid4())}
    worktree = agent.isolated_worktree(root, job)
    with agent.task_lock(root, job):
        pass
    return root, job, worktree


def test_gc_defaults_to_dry_run_and_preserves_clean_worktree(tmp_path):
    root, job, worktree = _repo_with_worktree(tmp_path)
    status, detail = gc.inspect_candidate(root, worktree, 0, apply=False)
    assert status == "would-remove"
    assert "clean, unlocked, merged" in detail
    assert worktree.is_dir()


def test_gc_skips_worktree_with_active_task_lock(tmp_path):
    root, job, worktree = _repo_with_worktree(tmp_path)
    with agent.task_lock(root, job):
        status, detail = gc.inspect_candidate(root, worktree, 0, apply=False)
    assert status == "skip"
    assert "lock is held" in detail
    assert worktree.is_dir()


def test_gc_skips_dirty_worktree(tmp_path):
    root, job, worktree = _repo_with_worktree(tmp_path)
    (worktree / "important-output.txt").write_text("keep me\n")
    status, detail = gc.inspect_candidate(root, worktree, 0, apply=False)
    assert status == "skip"
    assert "changes" in detail
    assert worktree.is_dir()


def test_gc_preserves_unmerged_task_branch(tmp_path):
    root, job, worktree = _repo_with_worktree(tmp_path)
    (worktree / "new-code.txt").write_text("unreviewed work\n")
    _git(worktree, "add", "new-code.txt")
    _git(worktree, "commit", "-m", "unreviewed work")
    status, detail = gc.inspect_candidate(root, worktree, 0, apply=False)
    assert status == "skip"
    assert "not merged" in detail
    assert worktree.is_dir()


def test_gc_apply_removes_only_clean_merged_worktree_and_branch(tmp_path):
    root, job, worktree = _repo_with_worktree(tmp_path)
    branch = f"oho/{job['run_id'][:8]}-{job['id'][:8]}"
    status, detail = gc.inspect_candidate(root, worktree, 0, apply=True)
    assert status == "removed"
    assert "removed worktree" in detail
    assert not worktree.exists()
    listed = _git(root, "branch", "--list", branch).stdout.strip()
    assert listed == ""


import plistlib
import stat

launchd_module_path = Path(__file__).resolve().parents[2] / "worker" / "launchd.py"
launchd_spec = importlib.util.spec_from_file_location("worker_launchd", launchd_module_path)
launchd = importlib.util.module_from_spec(launchd_spec)
launchd_spec.loader.exec_module(launchd)


def test_launchd_plist_runs_worker_and_restarts(tmp_path):
    plist_path = tmp_path / "LaunchAgents" / "com.tienganhcungcon.oho-worker.plist"
    launchd.write_plist(
        str(plist_path),
        python_path=str(tmp_path / "venv" / "bin" / "python"),
        worker_script=str(tmp_path / "repo" / "worker" / "agent.py"),
        config_path=str(tmp_path / "config" / "oho" / "config.json"),
        working_directory=str(tmp_path / "repo"),
        log_directory=str(tmp_path / "logs"),
        path_env="/opt/homebrew/bin:/usr/bin:/bin",
    )
    with plist_path.open("rb") as stream:
        document = plistlib.load(stream)
    assert document["Label"] == "com.tienganhcungcon.oho-worker"
    assert document["ProgramArguments"] == [
        str(tmp_path / "venv" / "bin" / "python"),
        str(tmp_path / "repo" / "worker" / "agent.py"),
        "run", "--config", str(tmp_path / "config" / "oho" / "config.json"),
    ]
    assert document["RunAtLoad"] is True
    assert document["KeepAlive"] is True
    assert document["ThrottleInterval"] == 10
    assert "CF_ACCESS_CLIENT_SECRET" not in document.get("EnvironmentVariables", {})
    assert stat.S_IMODE(plist_path.stat().st_mode) == 0o600


def test_launchd_plist_rejects_empty_path(tmp_path):
    with pytest.raises(ValueError):
        launchd.build_plist(
            python_path=str(tmp_path / "python"),
            worker_script=str(tmp_path / "agent.py"),
            config_path=str(tmp_path / "config.json"),
            working_directory=str(tmp_path),
            log_directory=str(tmp_path / "logs"),
            path_env="",
        )
