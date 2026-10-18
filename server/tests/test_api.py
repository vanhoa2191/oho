import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

DB_PATH = Path(tempfile.gettempdir()) / f"oho-tests-{os.getpid()}.sqlite3"
os.environ["OHO_DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["OHO_ADMIN_TOKEN"] = "test-admin-token-" + "a" * 40
os.environ["OHO_ENROLLMENT_TOKEN"] = "test-enroll-token-" + "b" * 40

from app.db import Base, engine
from app.main import app

AUTH = {"Authorization": "Bearer " + os.environ["OHO_ADMIN_TOKEN"]}
ENROLL = {"X-Enrollment-Token": os.environ["OHO_ENROLLMENT_TOKEN"]}

@pytest.fixture
def client():
    Base.metadata.drop_all(bind=engine)
    with TestClient(app) as c:
        yield c

def project(client):
    r = client.post("/api/v1/projects", headers=AUTH, json={
        "name": "OHO", "repo_url": "https://github.com/vanhoa2191/oho"})
    assert r.status_code == 200, r.text
    return r.json()["id"]

def enroll(client, name="mac1"):
    r = client.post("/api/v1/nodes/enroll", headers=ENROLL, json={
        "name": name, "models": ["codex"], "tags": ["macos"], "platform": "macos"})
    assert r.status_code == 200, r.text
    data = r.json()
    return {"X-Node-Id": data["node_id"], "X-Node-Token": data["node_token"]}

def test_health_and_auth(client):
    assert client.get("/health").json()["ok"]
    assert client.get("/api/v1/projects").status_code == 401
    assert client.post("/api/v1/nodes/enroll", json={"name": "x123"}).status_code == 401
    assert client.get("/api/v1/overview", headers=AUTH).json()["projects"] == 0

def test_project_url_validation(client):
    r = client.post("/api/v1/projects", headers=AUTH,
                    json={"name": "bad", "repo_url": "http://evil.com/a/b"})
    assert r.status_code == 422

def test_dag_validation(client):
    pid = project(client)
    data = {"project_id": pid, "title": "bad graph", "tasks": [
        {"key": "a", "argv": ["echo","a"],"depends_on":["b"]},
        {"key": "b", "argv": ["echo","b"],"depends_on":["a"]}
    ]}
    assert client.post("/api/v1/runs", headers=AUTH, json=data).status_code == 422

def test_task_lifecycle_and_dag(client):
    pid = project(client)
    node = enroll(client)
    run_response = client.post("/api/v1/runs", headers=AUTH, json={
        "project_id": pid, "title": "build", "tasks": [
            {"key": "lint", "argv": ["echo","lint"], "required_tags": ["macos"]},
            {"key": "test", "argv": ["pytest","-q"], "depends_on": ["lint"]}
        ]})
    assert run_response.status_code == 200, run_response.text
    run_id = run_response.json()["run"]["id"]
    claim1 = client.post("/api/v1/nodes/claim", headers=node).json()["task"]
    assert claim1["key"] == "lint"
    assert claim1["repo_url"] == "https://github.com/vanhoa2191/oho"
    assert client.post("/api/v1/nodes/claim", headers=node).json()["task"] is None
    assert client.post(f"/api/v1/nodes/tasks/{claim1['id']}/start", headers=node,
                       json={"lease_token": claim1["lease_token"]}).status_code == 200
    assert client.post(f"/api/v1/nodes/tasks/{claim1['id']}/renew", headers=node,
                       json={"lease_token": claim1["lease_token"]}).status_code == 200
    assert client.post(f"/api/v1/nodes/tasks/{claim1['id']}/complete", headers=node,
                       json={"lease_token": claim1["lease_token"], "success": True, "exit_code": 0,
                             "output": "success"}).status_code == 200
    assert client.post(f"/api/v1/nodes/tasks/{claim1['id']}/complete", headers=node,
                       json={"lease_token": claim1["lease_token"], "success": True}).status_code == 409
    assert client.post("/api/v1/nodes/claim", headers=node).json()["task"] is None
    approvals = client.get("/api/v1/approvals", headers=AUTH).json()
    candidate = next(t for t in approvals if t["key"] == "test")
    assert candidate["risk"] == "elevated"
    assert client.post(f"/api/v1/tasks/{candidate['id']}/approve", headers=AUTH).status_code == 200
    claim2 = client.post("/api/v1/nodes/claim", headers=node).json()["task"]
    assert claim2["key"] == "test"
    assert client.post(f"/api/v1/nodes/tasks/{claim2['id']}/complete", headers=node,
                       json={"lease_token": claim2["lease_token"], "success": True}).status_code == 200
    assert client.get(f"/api/v1/runs/{run_id}", headers=AUTH).json()["run"]["status"] == "succeeded"

def test_approval_gate(client):
    pid = project(client)
    node = enroll(client)
    r = client.post("/api/v1/runs", headers=AUTH, json={
        "project_id": pid, "title": "change", "tasks": [
            {"key": "unsafe", "argv": ["git","push"]}
        ]})
    assert r.status_code == 200, r.text
    task = r.json()["tasks"][0]
    assert task["risk"] == "merge"
    assert task["status"] == "awaiting_approval"
    assert client.post("/api/v1/nodes/claim", headers=node).json()["task"] is None
    assert client.get("/api/v1/approvals", headers=AUTH).json()[0]["id"] == task["id"]
    assert client.post(f"/api/v1/tasks/{task['id']}/approve", headers=AUTH).status_code == 200
    assert client.post("/api/v1/nodes/claim", headers=node).json()["task"]["id"] == task["id"]

def test_different_worker_cannot_complete(client):
    pid = project(client)
    n1 = enroll(client,"mac1")
    n2 = enroll(client,"linux1")
    r = client.post("/api/v1/runs", headers=AUTH, json={
        "project_id": pid, "title": "safe", "tasks": [{"key": "t", "argv": ["echo", "safe"]}]})
    assert r.status_code == 200
    task = client.post("/api/v1/nodes/claim", headers=n1).json()["task"]
    assert client.post(f"/api/v1/nodes/tasks/{task['id']}/complete", headers=n2,
                       json={"lease_token": task["lease_token"], "success": True}).status_code == 409

def test_reject(client):
    pid = project(client)
    r = client.post("/api/v1/runs", headers=AUTH, json={
        "project_id": pid, "title": "deploy release", "tasks": [{"key": "deploy", "argv": ["docker","compose","up"], "risk": "deploy"}]})
    task = r.json()["tasks"][0]
    assert client.post(f"/api/v1/tasks/{task['id']}/reject", headers=AUTH).status_code == 200
    assert client.get("/api/v1/runs/"+r.json()["run"]["id"], headers=AUTH).json()["run"]["status"] == "failed"


def test_model_registry_and_node_disable(client):
    node = enroll(client, "linux1")
    models = client.get("/api/v1/models", headers=AUTH)
    assert models.status_code == 200
    assert models.json()[0]["model"] == "codex"
    assert client.post(f"/api/v1/nodes/{node['X-Node-Id']}/disable", headers=AUTH).status_code == 200
    assert client.get("/api/v1/models", headers=AUTH).json() == []
    assert client.post("/api/v1/nodes/claim", headers=node).status_code == 401
