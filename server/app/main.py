from contextlib import asynccontextmanager
from datetime import timedelta
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .db import Base, engine, get_db, utcnow
from .models import Event, Node, Project, Run, Task
from .schemas import CompleteIn, EnrollIn, HeartbeatIn, LeaseIn, ProjectIn, RunIn
from .scheduler import claim, maintain, record, refresh_run, risk_of
from .security import require_config, admin, enrollment, token_hash, authenticate_node

@asynccontextmanager
async def lifespan(app: FastAPI):
    require_config()
    Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(title="OHO Control Plane", version="0.1.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)

@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"ok": True, "service": "oho"}

def project_out(p: Project):
    return {"id": p.id, "name": p.name, "repo_url": p.repo_url, "default_branch": p.default_branch}

def node_out(n: Node):
    return {"id": n.id, "name": n.name, "tags": n.tags, "models": n.models,
            "platform": n.platform, "enabled": n.enabled,
            "online": bool(n.last_seen and n.last_seen > utcnow() - timedelta(seconds=90)),
            "last_seen": n.last_seen.isoformat() + "Z"}

def task_out(t: Task):
    return {"id": t.id, "run_id": t.run_id, "key": t.key, "kind": t.kind, "argv": t.argv,
            "model": t.model, "depends_on": t.depends_on, "required_tags": t.required_tags,
            "risk": t.risk, "status": t.status, "approved": bool(t.approved_at),
            "approved_by": t.approved_by, "assigned_node_id": t.assigned_node_id,
            "attempts": t.attempts, "max_attempts": t.max_attempts,
            "output": t.output, "exit_code": t.exit_code,
            "updated_at": t.updated_at.isoformat() + "Z"}

def run_out(r: Run):
    return {"id": r.id, "project_id": r.project_id, "title": r.title, "status": r.status,
            "created_at": r.created_at.isoformat() + "Z"}

def worker(db: Session, x_node_id: str, x_node_token: str):
    return authenticate_node(db, x_node_id, x_node_token)

@app.post("/api/v1/nodes/enroll", dependencies=[Depends(enrollment)])
def enroll(payload: EnrollIn, db: Session = Depends(get_db)):
    if db.scalar(select(Node).where(Node.name == payload.name)):
        raise HTTPException(409, "Node already enrolled; use its existing credentials")
    token = secrets.token_urlsafe(48)
    node = Node(name=payload.name, token_hash=token_hash(token), tags=payload.tags,
                models=payload.models, platform=payload.platform)
    db.add(node)
    record(db, None, "node_enrolled", payload.name)
    db.commit()
    return {"node_id": node.id, "node_token": token}

@app.post("/api/v1/nodes/heartbeat")
def heartbeat(payload: HeartbeatIn, x_node_id: str = Header(...),
              x_node_token: str = Header(...), db: Session = Depends(get_db)):
    node = worker(db, x_node_id, x_node_token)
    node.tags = payload.tags
    node.models = payload.models
    node.platform = payload.platform
    node.last_seen = utcnow()
    db.commit()
    return {"ok": True}

@app.post("/api/v1/nodes/claim")
def node_claim(x_node_id: str = Header(...), x_node_token: str = Header(...),
               db: Session = Depends(get_db)):
    node = worker(db, x_node_id, x_node_token)
    node.last_seen = utcnow()
    job = claim(db, node)
    db.commit()
    return {"task": job}

def locked_task(db: Session, task_id: str, node: Node, lease_token: str) -> Task:
    task = db.execute(select(Task).where(Task.id == task_id).with_for_update()).scalar_one_or_none()
    if not task or task.assigned_node_id != node.id or not task.lease_token or not secrets.compare_digest(task.lease_token, lease_token):
        raise HTTPException(409, "Stale or invalid task lease")
    if task.status not in {"leased", "running"} or not task.lease_until or task.lease_until < utcnow():
        raise HTTPException(409, "Task lease expired")
    return task

@app.post("/api/v1/nodes/tasks/{task_id}/start")
def task_start(task_id: str, payload: LeaseIn, x_node_id: str = Header(...),
               x_node_token: str = Header(...), db: Session = Depends(get_db)):
    node = worker(db, x_node_id, x_node_token)
    task = locked_task(db, task_id, node, payload.lease_token)
    task.status = "running"
    task.updated_at = utcnow()
    record(db, task, "started", node.name)
    db.commit()
    return {"ok": True}

@app.post("/api/v1/nodes/tasks/{task_id}/renew")
def task_renew(task_id: str, payload: LeaseIn, x_node_id: str = Header(...),
               x_node_token: str = Header(...), db: Session = Depends(get_db)):
    node = worker(db, x_node_id, x_node_token)
    task = locked_task(db, task_id, node, payload.lease_token)
    task.lease_until = utcnow() + timedelta(seconds=90)
    node.last_seen = utcnow()
    db.commit()
    return {"ok": True}

@app.post("/api/v1/nodes/tasks/{task_id}/complete")
def task_complete(task_id: str, payload: CompleteIn, x_node_id: str = Header(...),
                  x_node_token: str = Header(...), db: Session = Depends(get_db)):
    node = worker(db, x_node_id, x_node_token)
    task = locked_task(db, task_id, node, payload.lease_token)
    task.exit_code = payload.exit_code
    task.output = payload.output[-20000:]
    task.updated_at = utcnow()
    task.status = "succeeded" if payload.success else ("pending" if task.attempts < task.max_attempts else "failed")
    task.lease_token = None
    task.lease_until = None
    task.assigned_node_id = None
    record(db, task, "completed" if payload.success else "attempt_failed", str(payload.exit_code))
    refresh_run(db, task.run_id)
    db.commit()
    return {"ok": True, "status": task.status}

@app.get("/api/v1/overview", dependencies=[Depends(admin)])
def overview(db: Session = Depends(get_db)):
    return {"projects": db.scalar(select(func.count()).select_from(Project)),
            "runs": db.scalar(select(func.count()).select_from(Run)),
            "nodes": db.scalar(select(func.count()).select_from(Node)),
            "pending_approvals": db.scalar(select(func.count()).select_from(Task).where(Task.status == "awaiting_approval"))}

@app.get("/api/v1/projects", dependencies=[Depends(admin)])
def projects(db: Session = Depends(get_db)):
    return [project_out(p) for p in db.scalars(select(Project).order_by(Project.created_at.desc())).all()]

@app.post("/api/v1/projects", dependencies=[Depends(admin)])
def create_project(payload: ProjectIn, db: Session = Depends(get_db)):
    if db.scalar(select(Project).where(Project.name == payload.name)):
        raise HTTPException(409, "Project name already exists")
    p = Project(**payload.model_dump())
    db.add(p)
    db.commit()
    return project_out(p)

@app.get("/api/v1/runs", dependencies=[Depends(admin)])
def runs(db: Session = Depends(get_db)):
    return [run_out(r) for r in db.scalars(select(Run).order_by(Run.created_at.desc()).limit(100)).all()]

@app.post("/api/v1/runs", dependencies=[Depends(admin)])
def create_run(payload: RunIn, db: Session = Depends(get_db)):
    if not db.get(Project, payload.project_id):
        raise HTTPException(404, "Project not found")
    run = Run(project_id=payload.project_id, title=payload.title)
    db.add(run)
    db.flush()
    for item in payload.tasks:
        risk = risk_of(item.kind, item.argv, item.risk)
        task = Task(run_id=run.id, key=item.key, kind=item.kind, argv=item.argv,
                    prompt=item.prompt, model=item.model,
                    depends_on=item.depends_on, required_tags=item.required_tags,
                    risk=risk, max_attempts=item.max_attempts,
                    timeout_seconds=item.timeout_seconds,
                    status="awaiting_approval" if risk != "normal" else "pending")
        db.add(task)
        db.flush()
        record(db, task, "task_created", f"risk={risk}")
    db.commit()
    return {"run": run_out(run), "tasks": [task_out(t) for t in db.scalars(select(Task).where(Task.run_id == run.id)).all()]}

@app.get("/api/v1/runs/{run_id}", dependencies=[Depends(admin)])
def run_detail(run_id: str, db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return {"run": run_out(run), "tasks": [task_out(t) for t in db.scalars(select(Task).where(Task.run_id == run_id)).all()]}

@app.get("/api/v1/nodes", dependencies=[Depends(admin)])
def nodes(db: Session = Depends(get_db)):
    return [node_out(n) for n in db.scalars(select(Node).order_by(Node.name)).all()]


@app.get("/api/v1/models", dependencies=[Depends(admin)])
def models(db: Session = Depends(get_db)):
    registry = {}
    for node in db.scalars(select(Node).where(Node.enabled == True)).all():
        for name in node.models:
            registry.setdefault(name, []).append({"node_id": node.id, "name": node.name,
                "online": bool(node.last_seen and node.last_seen > utcnow() - timedelta(seconds=90))})
    return [{"model": name, "nodes": matches} for name, matches in sorted(registry.items())]

@app.post("/api/v1/nodes/{node_id}/disable", dependencies=[Depends(admin)])
def disable_node(node_id: str, db: Session = Depends(get_db)):
    node = db.execute(select(Node).where(Node.id == node_id).with_for_update()).scalar_one_or_none()
    if not node:
        raise HTTPException(404, "Node not found")
    node.enabled = False
    record(db, None, "node_disabled", node.name)
    db.commit()
    return {"ok": True}

@app.get("/api/v1/approvals", dependencies=[Depends(admin)])
def approvals(db: Session = Depends(get_db)):
    return [task_out(t) for t in db.scalars(select(Task).where(Task.status == "awaiting_approval").order_by(Task.created_at)).all()]

@app.post("/api/v1/tasks/{task_id}/approve", dependencies=[Depends(admin)])
def approve(task_id: str, db: Session = Depends(get_db)):
    task = db.execute(select(Task).where(Task.id == task_id).with_for_update()).scalar_one_or_none()
    if not task or task.status != "awaiting_approval":
        raise HTTPException(409, "Task is not awaiting approval")
    task.approved_at = utcnow()
    task.approved_by = "admin"
    task.status = "pending"
    task.updated_at = utcnow()
    record(db, task, "approved", "admin")
    db.commit()
    return task_out(task)

@app.post("/api/v1/tasks/{task_id}/reject", dependencies=[Depends(admin)])
def reject(task_id: str, db: Session = Depends(get_db)):
    task = db.execute(select(Task).where(Task.id == task_id).with_for_update()).scalar_one_or_none()
    if not task or task.status != "awaiting_approval":
        raise HTTPException(409, "Task is not awaiting approval")
    task.status = "rejected"
    task.updated_at = utcnow()
    record(db, task, "rejected", "admin")
    refresh_run(db, task.run_id)
    db.commit()
    return task_out(task)

@app.post("/api/v1/tasks/{task_id}/cancel", dependencies=[Depends(admin)])
def cancel(task_id: str, db: Session = Depends(get_db)):
    task = db.execute(select(Task).where(Task.id == task_id).with_for_update()).scalar_one_or_none()
    if not task or task.status in {"succeeded", "failed", "cancelled", "rejected"}:
        raise HTTPException(409, "Task cannot be cancelled")
    task.status = "cancelled"
    task.lease_token = None
    task.lease_until = None
    task.updated_at = utcnow()
    record(db, task, "cancelled", "admin")
    refresh_run(db, task.run_id)
    db.commit()
    return task_out(task)

@app.get("/api/v1/events", dependencies=[Depends(admin)])
def events(limit: int = 30, db: Session = Depends(get_db)):
    rows = db.scalars(select(Event).order_by(Event.created_at.desc()).limit(max(1, min(limit, 100)))).all()
    return [{"id": e.id, "task_id": e.task_id, "kind": e.kind, "detail": e.detail,
             "created_at": e.created_at.isoformat() + "Z"} for e in rows]

@app.post("/api/v1/maintenance", dependencies=[Depends(admin)])
def maintenance(db: Session = Depends(get_db)):
    maintain(db)
    db.commit()
    return {"ok": True}
