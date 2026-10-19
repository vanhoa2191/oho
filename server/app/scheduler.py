import secrets
from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import utcnow
from .models import Event, Node, Run, Task

LIVE = {"pending", "awaiting_approval", "leased", "running"}
TARGET_NODE_PREFIX = "__oho_target_node__:"
RISK_RANK = {"normal": 0, "elevated": 1, "merge": 2, "deploy": 3}
ELEVATED_EXECUTABLES = {"docker", "sudo", "su", "rm", "sh", "bash", "zsh", "powershell", "pwsh",
    "kubectl", "terraform", "systemctl", "ssh", "scp", "chmod", "chown",
    "curl", "wget", "make", "eval", "python", "python3", "node", "npx", "npm", "pytest", "ruff", "mypy", "uv"}

def risk_of(kind: str, argv: list[str], requested: str) -> str:
    risk = requested
    if kind == "agent":
        risk = max(risk, "elevated", key=lambda x: RISK_RANK[x])
    else:
        executable = argv[0].lower() if argv else ""
        if executable in ELEVATED_EXECUTABLES:
            risk = max(risk, "elevated", key=lambda x: RISK_RANK[x])
        if executable == "git" and len(argv) > 1 and argv[1] in {
            "push", "merge", "rebase", "reset", "clean", "checkout", "switch", "branch", "tag", "config"}:
            risk = max(risk, "merge", key=lambda x: RISK_RANK[x])
        if executable in {"deploy", "ansible-playbook", "helm"}:
            risk = "deploy"
    return risk

def record(db: Session, task: Task | None, kind: str, detail: str = ""):
    db.add(Event(task_id=task.id if task else None, kind=kind, detail=detail[:500]))

def refresh_run(db: Session, run_id: str):
    db.flush()
    run = db.get(Run, run_id)
    if not run:
        return
    statuses = list(db.scalars(select(Task.status).where(Task.run_id == run_id)).all())
    if not statuses:
        return
    if all(s == "succeeded" for s in statuses):
        run.status = "succeeded"
    elif any(s in LIVE for s in statuses):
        run.status = "running"
    elif any(s in {"failed", "blocked", "rejected"} for s in statuses):
        run.status = "failed"
    elif all(s == "cancelled" for s in statuses):
        run.status = "cancelled"

def maintain(db: Session):
    now = utcnow()
    expired = db.scalars(
        select(Task).where(Task.status.in_(["leased", "running"]), Task.lease_until < now)
        .with_for_update(skip_locked=True)
    ).all()
    touched = set()
    for task in expired:
        task.status = "failed" if task.attempts >= task.max_attempts else "pending"
        task.lease_token = None
        task.lease_until = None
        task.assigned_node_id = None
        task.updated_at = now
        record(db, task, "lease_expired", str(task.attempts))
        touched.add(task.run_id)
    for run_id in touched:
        refresh_run(db, run_id)

def claim(db: Session, node: Node) -> dict | None:
    maintain(db)
    now = utcnow()
    tasks = db.scalars(
        select(Task).join(Run, Task.run_id == Run.id)
        .where(Run.status == "running", Task.status.in_(["pending", "awaiting_approval"]))
        .order_by(Task.created_at, Task.id)
        .with_for_update(skip_locked=True)
    ).all()
    for task in tasks:
        target_node = next(
            (tag[len(TARGET_NODE_PREFIX):] for tag in task.required_tags
             if tag.startswith(TARGET_NODE_PREFIX)),
            None,
        )
        if target_node is not None and node.name != target_node:
            continue
        required_tags = {
            tag for tag in task.required_tags
            if not tag.startswith(TARGET_NODE_PREFIX)
        }
        if not required_tags.issubset(set(node.tags)):
            continue
        if task.kind == "agent" and task.model not in node.models:
            continue
        siblings = dict(db.execute(select(Task.key, Task.status).where(Task.run_id == task.run_id)).all())
        if any(siblings.get(dep) in {"failed", "blocked", "rejected", "cancelled"} for dep in task.depends_on):
            task.status = "blocked"
            record(db, task, "dependency_blocked")
            refresh_run(db, task.run_id)
            continue
        if any(siblings.get(dep) != "succeeded" for dep in task.depends_on):
            continue
        if task.risk != "normal" and not task.approved_at:
            if task.status != "awaiting_approval":
                task.status = "awaiting_approval"
                record(db, task, "approval_requested", task.risk)
            continue
        if task.attempts >= task.max_attempts:
            task.status = "failed"
            record(db, task, "attempts_exhausted")
            continue
        token = secrets.token_urlsafe(32)
        task.assigned_node_id = node.id
        task.lease_token = token
        task.lease_until = now + timedelta(seconds=90)
        task.attempts += 1
        task.status = "leased"
        task.updated_at = now
        record(db, task, "claimed", f"{node.name}, attempt {task.attempts}")
        run = db.get(Run, task.run_id)
        return {"id": task.id, "run_id": task.run_id, "key": task.key,
                "kind": task.kind, "argv": task.argv, "prompt": task.prompt,
                "model": task.model, "risk": task.risk, "approved": bool(task.approved_at),
                "lease_token": token, "timeout_seconds": task.timeout_seconds,
                "repo_url": run.project.repo_url, "default_branch": run.project.default_branch}
    return None
