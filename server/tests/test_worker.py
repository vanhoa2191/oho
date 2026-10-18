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
