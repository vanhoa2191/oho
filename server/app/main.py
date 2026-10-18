import secrets
from contextlib import asynccontextmanager
from datetime import timedelta
from fastapi import FastAPI, Depends, Header, HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from .db import Base, engine, get_db, utcnow
from .models import Node, Project, Run, Task
from .schemas import EnrollIn, ProjectIn, RunIn, LeaseIn, CompleteIn
from .security import require_config, admin, enrollment, token_hash, authenticate_node

@asynccontextmanager
async def lifespan(app):
    require_config()
    Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(title="OHO", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"ok": True}

@app.post("/api/projects", dependencies=[Depends(admin)])
def create_project(p: ProjectIn, db: Session = Depends(get_db)):
    obj = Project(**p.model_dump())
    db.add(obj)
    db.commit()
    return {"id": obj.id, "name": obj.name}

@app.get("/api/projects", dependencies=[Depends(admin)])
def projects(db: Session = Depends(get_db)):
    return [{"id": x.id, "name": x.name, "repo_url": x.repo_url} for x in db.scalars(select(Project)).all()]

@app.post("/api/nodes/enroll", dependencies=[Depends(enrollment)])
def enroll(n: EnrollIn, db: Session = Depends(get_db)):
    if db.scalar(select(Node).where(Node.name == n.name)):
        raise HTTPException(409, "Node already exists")
    token = secrets.token_urlsafe(48)
    node = Node(name=n.name, token_hash=token_hash(token), tags=n.tags, models=n.models, platform=n.platform)
    db.add(node)
    db.commit()
    return {"node_id": node.id, "node_token": token}

@app.get("/api/nodes", dependencies=[Depends(admin)])
def nodes(db: Session = Depends(get_db)):
    return [{"id": n.id, "name": n.name, "tags": n.tags, "models": n.models, "last_seen": str(n.last_seen)}
            for n in db.scalars(select(Node)).all()]

@app.post("/api/runs", dependencies=[Depends(admin)])
def create_run(payload: RunIn, db: Session = Depends(get_db)):
    if not db.get(Project, payload.project_id):
        raise HTTPException(404, "Project not found")
    run = Run(project_id=payload.project_id, title=payload.title)
    db.add(run)
    db.flush()
    for item in payload.tasks:
        risk = item.risk
        if item.kind == "agent" or item.argv[0] not in {"git", "pytest", "echo"}:
            risk = "elevated" if risk == "normal" else risk
        if item.kind == "command" and any(arg in {"push", "merge", "reset", "clean"} for arg in item.argv):
            risk = "merge"
        db.add(Task(run_id=run.id, key=item.key, kind=item.kind, argv=item.argv,
                    prompt=item.prompt, model=item.model, depends_on=item.depends_on,
                    required_tags=item.required_tags, risk=risk,
                    status="awaiting_approval" if risk != "normal" else "pending"))
    db.commit()
    return {"id": run.id}

@app.get("/api/runs", dependencies=[Depends(admin)])
def runs(db: Session = Depends(get_db)):
    return [{"id": r.id, "title": r.title, "status": r.status} for r in db.scalars(select(Run)).all()]

@app.get("/api/runs/{run_id}", dependencies=[Depends(admin)])
def run_detail(run_id: str, db: Session = Depends(get_db)):
    return [{"id": t.id, "key": t.key, "status": t.status, "risk": t.risk, "output": t.output}
            for t in db.scalars(select(Task).where(Task.run_id == run_id)).all()]

@app.post("/api/tasks/{task_id}/approve", dependencies=[Depends(admin)])
def approve(task_id: str, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task or task.status != "awaiting_approval":
        raise HTTPException(409, "Not awaiting approval")
    task.approved_at = utcnow()
    task.approved_by = "admin"
    task.status = "pending"
    db.commit()
    return {"ok": True}

@app.post("/api/nodes/claim")
def claim(x_node_id: str = Header(...), x_node_token: str = Header(...), db: Session = Depends(get_db)):
    node = authenticate_node(db, x_node_id, x_node_token)
    node.last_seen = utcnow()
    now = utcnow()
    for task in db.scalars(select(Task).where(Task.status == "pending").with_for_update(skip_locked=True)).all():
        if not set(task.required_tags).issubset(set(node.tags)) or (task.kind == "agent" and task.model not in node.models):
            continue
        if task.risk != "normal" and not task.approved_at:
            continue
        siblings = {t.key: t.status for t in db.scalars(select(Task).where(Task.run_id == task.run_id)).all()}
        if not all(siblings.get(dep) == "succeeded" for dep in task.depends_on):
            continue
        token = secrets.token_urlsafe(32)
        task.lease_token, task.lease_until = token, now + timedelta(seconds=90)
        task.assigned_node_id, task.status = node.id, "leased"
        task.attempts += 1
        db.commit()
        return {"task": {"id": task.id, "kind": task.kind, "argv": task.argv,
                "prompt": task.prompt, "model": task.model, "lease_token": token,
                "timeout_seconds": task.timeout_seconds}}
    db.commit()
    return {"task": None}

@app.post("/api/nodes/tasks/{task_id}/complete")
def complete(task_id: str, payload: CompleteIn, x_node_id: str = Header(...),
             x_node_token: str = Header(...), db: Session = Depends(get_db)):
    node = authenticate_node(db, x_node_id, x_node_token)
    task = db.get(Task, task_id)
    if not task or task.assigned_node_id != node.id or task.lease_token != payload.lease_token or task.lease_until < utcnow():
        raise HTTPException(409, "Invalid or expired lease")
    task.status = "succeeded" if payload.success else "failed"
    task.output = payload.output[-20000:]
    task.exit_code = payload.exit_code
    task.lease_token = None
    db.commit()
    return {"ok": True}
