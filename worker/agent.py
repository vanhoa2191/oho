#!/usr/bin/env python3
"""OHO node worker. Runs approved tasks in configured project directories only.

Security model: non-shell subprocess; strict command allowlist; operations affecting
remotes/deployment/privileges refused even if server-side human approval was given.
A future signed capability protocol is required to permit privileged operations.
"""
import argparse
import asyncio
import fcntl
import json
import os
import platform
import secrets
import signal
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx

NODE_STATE = Path.home() / ".config" / "oho" / "node.json"
SAFE_GIT_ARGS = {("status",), ("status", "--short"), ("log", "-5", "--oneline"), ("diff", "--stat"), ("rev-parse", "HEAD")}
ALLOWED_CMDS = {"pytest", "echo", "git"}
SAFE_WORKSPACE_FLAGS = {
    "codex": ["exec", "--sandbox", "workspace-write", "--skip-git-repo-check"],
    # Other CLIs differ by version; only enable after explicit configuration.
}

class WorkerError(Exception):
    pass

def safe_path(raw: str) -> Path:
    p = Path(raw).expanduser().resolve(strict=True)
    if not p.is_dir():
        raise WorkerError("Project root must be an existing directory")
    if not (p / ".git").exists():
        raise WorkerError("Project root must be a checked-out Git repository")
    return p

def validate_repo(repo: str) -> str:
    u = urlparse(repo)
    if u.scheme != "https" or u.netloc != "github.com" or u.query or u.fragment or not u.path or len(u.path.strip("/").split("/")) != 2:
        raise WorkerError("Only HTTPS GitHub repositories are supported")
    return repo.removesuffix(".git")

AUTH_ENV_BY_ADAPTER = {
    "codex": {"OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_HOME"},
    "claude": {"ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CLAUDE_CONFIG_DIR"},
    "gemini": {
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION", "GOOGLE_GENAI_USE_VERTEXAI",
        "GOOGLE_APPLICATION_CREDENTIALS",
    },
    "opencode": {
        "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY", "GOOGLE_API_KEY", "OPENCODE_CONFIG",
        "OPENCODE_CONFIG_DIR",
    },
}


def available_models(config: dict) -> list[str]:
    """Advertise only profiles whose CLI executable is present on this worker."""
    profiles = config.get("agent_profiles") or {}
    if profiles:
        return sorted(
            name for name, profile in profiles.items()
            if isinstance(profile, dict)
            and isinstance(profile.get("binary"), str)
            and bool(shutil.which(profile["binary"]))
        )
    binaries = config.get("agent_binaries") or {}
    if binaries:
        return sorted(
            name for name, binary in binaries.items()
            if isinstance(binary, str) and bool(shutil.which(binary))
        )
    return []


def agent_profile(model: str, config: dict) -> dict:
    profile = (config.get("agent_profiles") or {}).get(model)
    if profile is not None:
        if not isinstance(profile, dict):
            raise WorkerError(f"Invalid agent profile: {model}")
        return profile
    # Backward-compatible Codex-only configuration used by existing workers.
    binary = (config.get("agent_binaries") or {}).get(model)
    if model == "codex" and binary:
        return {"adapter": "codex", "binary": binary}
    raise WorkerError(f"Adapter profile is not configured: {model}")


def validate_agent_profiles(config: dict) -> None:
    profiles = config.get("agent_profiles") or {}
    if not isinstance(profiles, dict):
        raise WorkerError("agent_profiles must be an object")
    for name, profile in profiles.items():
        if not isinstance(name, str) or not name or len(name) > 60 or any(ord(c) < 32 for c in name):
            raise WorkerError("Invalid agent profile name")
        if not isinstance(profile, dict):
            raise WorkerError(f"Invalid agent profile: {name}")
        adapter = profile.get("adapter")
        binary = profile.get("binary")
        if adapter not in {"codex", "claude", "gemini", "opencode"}:
            raise WorkerError(f"Unsupported adapter for profile: {name}")
        if not isinstance(binary, str) or not binary or Path(binary).name != adapter:
            raise WorkerError(f"Profile {name} binary must resolve to the {adapter} executable")
        model_id = profile.get("model", "")
        if not isinstance(model_id, str) or len(model_id) > 120 or any(ord(c) < 32 for c in model_id):
            raise WorkerError(f"Invalid provider model for profile: {name}")
        max_turns = profile.get("max_turns", 5)
        if adapter == "claude" and (not isinstance(max_turns, int) or not 1 <= max_turns <= 20):
            raise WorkerError(f"Claude max_turns for {name} must be 1-20")
        credential_env = profile.get("credential_env", [])
        if not isinstance(credential_env, list) or not all(isinstance(k, str) for k in credential_env):
            raise WorkerError(f"credential_env for {name} must be a list of environment variable names")
        if not set(credential_env).issubset(AUTH_ENV_BY_ADAPTER[adapter]):
            raise WorkerError(f"credential_env contains a variable not allowed for adapter {adapter}")


def command_for(job: dict, config: dict) -> list[str]:
    kind = job.get("kind")
    if kind == "command":
        argv = job.get("argv") or []
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise WorkerError("Malformed argv")
        exe = argv[0]
        allowed = set(config.get("allowed_commands") or ["pytest", "echo", "git"]) & ALLOWED_CMDS
        if exe not in allowed:
            raise WorkerError(f"Executable not allowed: {exe}")
        if exe == "git":
            if tuple(argv[1:]) not in SAFE_GIT_ARGS:
                raise WorkerError("Git operation or arguments not allowlisted")
        if any("\x00" in item or "\n" in item for item in argv):
            raise WorkerError("Unsafe command argument")
        return argv
    if kind == "agent":
        model = str(job.get("model") or "")
        prompt = str(job.get("prompt") or "")
        if not prompt or len(prompt) > 12000:
            raise WorkerError("Invalid prompt")
        if not job.get("approved"):
            raise WorkerError("Agent work requires explicit approval")
        profile = agent_profile(model, config)
        adapter = profile.get("adapter")
        binary = profile.get("binary")
        if not isinstance(binary, str) or Path(binary).name != adapter:
            raise WorkerError("Agent profile executable does not match its adapter")
        provider_model = str(profile.get("model") or "")
        model_args = ["--model", provider_model] if provider_model else []

        if adapter == "codex":
            return [binary, *SAFE_WORKSPACE_FLAGS["codex"], *model_args, prompt]
        if adapter == "claude":
            # Edit-capable, non-interactive mode. Bash is explicitly disabled.
            return [
                binary, "--print", "--output-format", "text",
                "--permission-mode", "acceptEdits",
                "--max-turns", str(profile.get("max_turns", 5)),
                "--disallowedTools", "Bash",
                *model_args, prompt,
            ]
        if adapter == "gemini":
            # Sandbox and auto_edit permit file edits but do not auto-approve all tools.
            return [
                binary, "--sandbox", "--approval-mode", "auto_edit",
                "--output-format", "text", *model_args,
                "--prompt", prompt,
            ]
        if adapter == "opencode":
            if not provider_model:
                raise WorkerError("OpenCode profile requires provider/model in its model field")
            return [binary, "run", "--format", "json", *model_args, prompt]
        raise WorkerError("Unsupported agent adapter")
    raise WorkerError("Unsupported task kind")


def agent_process_env(job: dict, config: dict) -> dict[str, str]:
    """Build a minimal environment and pass only credentials for the chosen adapter."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "LANG": "C.UTF-8",
        "CI": "true",
        "PYTHONUNBUFFERED": "1",
    }
    # Command tasks use only the base environment and must not require an
    # agent profile. Only agent tasks receive explicitly allowlisted credentials.
    if job.get("kind") != "agent":
        return env
    profile = agent_profile(str(job.get("model") or ""), config)
    adapter = profile["adapter"]
    for key in profile.get("credential_env", []):
        value = os.environ.get(key)
        if value:
            env[key] = value
    if adapter == "opencode":
        # Initial pilot is edit-only: do not auto-approve arbitrary shell commands.
        env["OPENCODE_PERMISSION"] = json.dumps({
            "*": "deny",
            "read": "allow",
            "edit": "allow",
            "glob": "allow",
            "grep": "allow",
            "list": "allow",
            "lsp": "allow",
            "bash": "deny",
            "webfetch": "deny",
            "websearch": "deny",
            "task": "deny",
            "external_directory": "deny",
            "skill": "deny",
            "question": "deny",
        }, separators=(",", ":"))
    return env

def load_state() -> dict:
    if not NODE_STATE.is_file():
        raise WorkerError("Not enrolled. Run: python agent.py enroll --config config.json")
    return json.loads(NODE_STATE.read_text())

def save_state(state: dict):
    NODE_STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(NODE_STATE), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(state, f)
    os.chmod(NODE_STATE, 0o600)

def config_load(config_path: str) -> dict:
    cfg = json.loads(Path(config_path).expanduser().read_text())
    validate_agent_profiles(cfg)
    url = cfg.get("server_url", "").rstrip("/")
    parsed = urlparse(url)
    if not ((parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password)
            or (parsed.scheme == "http" and parsed.hostname == "127.0.0.1")):
        raise WorkerError("Use HTTPS URL for the control plane")
    if not cfg.get("name") or not cfg.get("project_roots"):
        raise WorkerError("Configure node name and project_roots")
    return cfg

def headers(state: dict) -> dict:
    return {"X-Node-Id": state["node_id"], "X-Node-Token": state["node_token"]}

def request_headers(extra: dict | None = None) -> dict:
    """Add optional Cloudflare Access Service Token headers without logging secrets.

    Configure both CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET in the worker
    process environment when the OHO hostname is protected by Access. Supplying
    only one is a configuration error; supplying neither supports local/private
    deployments that do not require Cloudflare Access.
    """
    headers = dict(extra or {})
    client_id = os.getenv("CF_ACCESS_CLIENT_ID", "").strip()
    client_secret = os.getenv("CF_ACCESS_CLIENT_SECRET", "").strip()
    if bool(client_id) != bool(client_secret):
        raise WorkerError("Cloudflare Access requires both CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET")
    if client_id and client_secret:
        headers["CF-Access-Client-Id"] = client_id
        headers["CF-Access-Client-Secret"] = client_secret
    return headers

def api(client: httpx.Client, method: str, path: str, **kw):
    kw["headers"] = request_headers(kw.get("headers"))
    resp = client.request(method, path, **kw)
    resp.raise_for_status()
    return resp.json()

def enroll(cfg: dict, enrollment_token: str):
    with httpx.Client(base_url=cfg["server_url"].rstrip("/"), timeout=20.0) as client:
        result = api(client, "POST", "/api/v1/nodes/enroll",
                     headers={"X-Enrollment-Token": enrollment_token},
                     json={"name": cfg["name"], "platform": cfg.get("platform", platform.system()),
                           "tags": cfg.get("tags", []), "models": available_models(cfg)})
        save_state(result)
        print("Enrolled node:", cfg["name"], result["node_id"])

def isolated_worktree(root: Path, job: dict) -> Path:
    """Create/reuse a per-task git worktree for safe branch isolation."""
    task_id = str(job["id"])
    run_id = str(job["run_id"])
    if not all(ch.isalnum() or ch == "-" for ch in task_id + run_id):
        raise WorkerError("Invalid task identity")
    path = root.parent / ".oho-worktrees" / root.name / task_id
    branch = f"oho/{run_id[:8]}-{task_id[:8]}"
    if path.exists():
        if not (path / ".git").exists():
            raise WorkerError("Stale non-Git worktree path; manual inspection required")
        return path
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    result = subprocess.run(["git", "-C", str(root), "worktree", "add",
                             "-b", branch, str(path), "HEAD"],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode != 0:
        raise WorkerError("Cannot create dedicated worktree: " + result.stderr[-400:])
    return path

def configured_root(job: dict, cfg: dict) -> Path:
    project_roots = {validate_repo(k): safe_path(v) for k, v in cfg["project_roots"].items()}
    repo = validate_repo(job["repo_url"])
    if repo not in project_roots:
        raise WorkerError(f"Repository not configured: {repo}")
    return project_roots[repo]


def task_lock_path(root: Path, job: dict) -> Path:
    task_id = str(job["id"])
    if not task_id or not all(ch.isalnum() or ch == "-" for ch in task_id):
        raise WorkerError("Invalid task identity for lock")
    base = root.parent / ".oho-worktrees" / root.name
    if base.parent.is_symlink() or base.is_symlink():
        raise WorkerError("Worktree base must not be a symlink")
    lock_dir = base / ".locks"
    if lock_dir.is_symlink():
        raise WorkerError("Worktree lock directory must not be a symlink")
    return lock_dir / f"{task_id}.lock"


@contextmanager
def task_lock(root: Path, job: dict):
    """Hold an OS lock until the server acknowledges task completion.

    The GC process takes the same non-blocking lock before inspecting/removing a
    worktree. flock is released automatically if the worker process crashes.
    """
    lock_path = task_lock_path(root, job)
    fd = None
    try:
        lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(lock_path), flags, 0o600)
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
    except OSError as ex:
        if fd is not None:
            os.close(fd)
        raise WorkerError(f"Cannot acquire task worktree lock: {ex}") from ex
    try:
        yield lock_path
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def run_job(job: dict, cfg: dict, state: dict, client: httpx.Client):
    token = job["lease_token"]
    task_id = job["id"]
    payload = {"lease_token": token}
    root = isolated_worktree(configured_root(job, cfg), job)
    cmd = command_for(job, cfg)
    api(client, "POST", f"/api/v1/nodes/tasks/{task_id}/start", headers=headers(state), json=payload)
    deadline = time.monotonic() + int(job.get("timeout_seconds", 900))
    proc = subprocess.Popen(cmd, cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, start_new_session=True, env=agent_process_env(job, cfg))
    output = bytearray()
    import threading
    def consume():
        assert proc.stdout is not None
        for chunk in iter(lambda: proc.stdout.read(4096), b""):
            if len(output) < 38000:
                output.extend(chunk[:38000-len(output)])
    reader = threading.Thread(target=consume, daemon=True)
    reader.start()
    timed_out = False
    while proc.poll() is None:
        if time.monotonic() > deadline:
            timed_out = True
            os.killpg(proc.pid, signal.SIGKILL)
            break
        time.sleep(15)
        try:
            api(client, "POST", f"/api/v1/nodes/tasks/{task_id}/renew",
                headers=headers(state), json=payload)
        except (httpx.HTTPError, KeyError):
            # Lease is no longer valid: stop immediately.
            os.killpg(proc.pid, signal.SIGKILL)
            timed_out = True
            break
    proc.wait()
    reader.join(timeout=3)
    return {"lease_token": token, "success": proc.returncode == 0 and not timed_out,
            "exit_code": proc.returncode, "output": ("Worktree: " + str(root) + "\n" + output.decode(errors="replace"))[-38000:]}

def daemon(cfg: dict):
    state = load_state()
    with httpx.Client(base_url=cfg["server_url"].rstrip("/"), timeout=25.0) as client:
        while True:
            try:
                api(client, "POST", "/api/v1/nodes/heartbeat",
                    headers=headers(state),
                    json={"tags": cfg.get("tags", []), "models": available_models(cfg),
                          "platform": cfg.get("platform", platform.system())})
                result = api(client, "POST", "/api/v1/nodes/claim", headers=headers(state))
                job = result.get("task")
                if job:
                    print("Received task:", job.get("id"), flush=True)
                    root = configured_root(job, cfg)
                    with task_lock(root, job):
                        try:
                            answer = run_job(job, cfg, state, client)
                        except Exception as ex:
                            answer = {"lease_token": job["lease_token"], "success": False,
                                      "exit_code": -1, "output": f"Worker rejected task: {ex}"}
                        # Keep the task lock until the API acknowledges completion.
                        # Retry transient failures so GC cannot remove the worktree
                        # while the server may still consider the lease active.
                        while True:
                            try:
                                api(client, "POST", f"/api/v1/nodes/tasks/{job['id']}/complete",
                                    headers=headers(state), json=answer)
                                break
                            except httpx.HTTPStatusError as ex:
                                if ex.response.status_code < 500:
                                    print("Task completion rejected:", ex.response.status_code,
                                          file=sys.stderr, flush=True)
                                    break
                                print("Task completion server error; retrying in 5s",
                                      file=sys.stderr, flush=True)
                                time.sleep(5)
                            except httpx.TransportError:
                                print("Task completion connection failed; retrying in 5s",
                                      file=sys.stderr, flush=True)
                                time.sleep(5)
                else:
                    time.sleep(max(2, min(int(cfg.get("poll_seconds", 5)), 60)))
            except (httpx.HTTPError, WorkerError) as ex:
                print("Worker warning:", str(ex)[:200], file=sys.stderr, flush=True)
                time.sleep(10)

def main():
    parser = argparse.ArgumentParser(description="OHO worker")
    parser.add_argument("action", choices=["enroll", "run"])
    parser.add_argument("--config", required=True)
    parser.add_argument("--enrollment-token", default="")
    args = parser.parse_args()
    cfg = config_load(args.config)
    if args.action == "enroll":
        token = args.enrollment_token or os.getenv("OHO_ENROLLMENT_TOKEN", "")
        if not token:
            raise WorkerError("Enrollment token missing")
        enroll(cfg, token)
    else:
        daemon(cfg)

if __name__ == "__main__":
    main()
