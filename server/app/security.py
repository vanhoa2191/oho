import hashlib
import hmac
import os
from fastapi import Header, HTTPException, status
from sqlalchemy.orm import Session

from .models import Node

def token_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()

def _secure_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())

def require_config():
    for key in ("OHO_ADMIN_TOKEN", "OHO_ENROLLMENT_TOKEN"):
        value = os.getenv(key, "")
        if len(value) < 24 or value.startswith("REPLACE_"):
            raise RuntimeError(f"{key} must be a unique strong secret (>=24 chars)")
    if _secure_equals(os.getenv("OHO_ADMIN_TOKEN", ""), os.getenv("OHO_ENROLLMENT_TOKEN", "")):
        raise RuntimeError("Admin and enrollment secrets must differ")

def admin(authorization: str = Header(default="")):
    expected = os.getenv("OHO_ADMIN_TOKEN", "")
    if not authorization.startswith("Bearer ") or not _secure_equals(authorization[7:], expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin credentials")
    return "admin"

def enrollment(x_enrollment_token: str = Header(default="")):
    if not _secure_equals(x_enrollment_token, os.getenv("OHO_ENROLLMENT_TOKEN", "")):
        raise HTTPException(status_code=401, detail="Invalid enrollment token")

def authenticate_node(db: Session, node_id: str, node_token: str) -> Node:
    node = db.get(Node, node_id)
    if not node or not node.enabled or not _secure_equals(token_hash(node_token), node.token_hash):
        raise HTTPException(status_code=401, detail="Invalid node credentials")
    return node
