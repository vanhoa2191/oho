import hashlib
import hmac
import os
from functools import lru_cache
from urllib.parse import urlsplit

import jwt
from jwt import PyJWKClient, PyJWKClientConnectionError
from fastapi import Header, HTTPException, status
from sqlalchemy.orm import Session

from .models import Node


def token_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _secure_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def auth_mode() -> str:
    return os.getenv("OHO_AUTH_MODE", "bearer").strip().lower()


def _access_config() -> tuple[str, str, str, str]:
    issuer = os.getenv("OHO_ACCESS_ISSUER", "").rstrip("/")
    audience = os.getenv("OHO_ACCESS_AUD", "").strip()
    admin_email = os.getenv("OHO_ADMIN_EMAIL", "").strip()
    jwks_url = os.getenv("OHO_ACCESS_JWKS_URL", "").strip()
    return issuer, audience, admin_email, jwks_url


@lru_cache(maxsize=4)
def _jwks_client(issuer: str, jwks_url: str) -> PyJWKClient:
    url = jwks_url or issuer + "/cdn-cgi/access/certs"
    return PyJWKClient(url, cache_jwk_set=True, lifespan=300, timeout=5)


def verify_access_assertion(assertion: str) -> dict:
    issuer, audience, admin_email, jwks_url = _access_config()
    if not issuer or not audience or not admin_email:
        raise RuntimeError("Cloudflare Access issuer, audience and admin email must be configured")

    signing_key = _jwks_client(issuer, jwks_url).get_signing_key_from_jwt(assertion)
    claims = jwt.decode(
        assertion,
        signing_key.key,
        algorithms=["RS256"],
        audience=audience,
        issuer=issuer,
        leeway=5,
        options={"require": ["exp", "iat", "aud", "iss"]},
    )
    return claims


def require_config():
    enrollment_token = os.getenv("OHO_ENROLLMENT_TOKEN", "")
    if len(enrollment_token) < 24 or enrollment_token.startswith("REPLACE_"):
        raise RuntimeError("OHO_ENROLLMENT_TOKEN must be a unique strong secret (>=24 chars)")

    mode = auth_mode()
    if mode == "bearer":
        admin_token = os.getenv("OHO_ADMIN_TOKEN", "")
        if len(admin_token) < 24 or admin_token.startswith("REPLACE_"):
            raise RuntimeError("OHO_ADMIN_TOKEN must be a unique strong secret (>=24 chars) in bearer mode")
        if _secure_equals(admin_token, enrollment_token):
            raise RuntimeError("Admin and enrollment secrets must differ")
    elif mode == "cloudflare_access":
        issuer, audience, admin_email, jwks_url = _access_config()
        parsed = urlsplit(issuer)
        if parsed.scheme != "https" or not parsed.hostname or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
            raise RuntimeError("OHO_ACCESS_ISSUER must be an HTTPS Cloudflare Access team issuer URL")
        if not audience or len(audience) > 500:
            raise RuntimeError("OHO_ACCESS_AUD must contain the Access application's Audience Tag")
        if "@" not in admin_email or len(admin_email) > 254:
            raise RuntimeError("OHO_ADMIN_EMAIL must be the exact administrator email")
        if jwks_url and (urlsplit(jwks_url).scheme != "https" or not urlsplit(jwks_url).hostname):
            raise RuntimeError("OHO_ACCESS_JWKS_URL, when set, must be HTTPS")
    else:
        raise RuntimeError("OHO_AUTH_MODE must be 'bearer' or 'cloudflare_access'")


def admin(
    authorization: str = Header(default=""),
    cf_access_jwt_assertion: str = Header(default=""),
):
    mode = auth_mode()
    if mode == "cloudflare_access":
        if not cf_access_jwt_assertion:
            raise HTTPException(status_code=401, detail="Cloudflare Access identity required")
        try:
            claims = verify_access_assertion(cf_access_jwt_assertion)
        except PyJWKClientConnectionError:
            raise HTTPException(status_code=503, detail="Cloudflare Access key service temporarily unavailable")
        except (jwt.PyJWTError, ValueError, OSError):
            raise HTTPException(status_code=401, detail="Invalid Cloudflare Access identity")
        _, _, admin_email, _ = _access_config()
        email = claims.get("email")
        if not isinstance(email, str) or not hmac.compare_digest(
            email.strip().casefold().encode(), admin_email.casefold().encode()
        ):
            raise HTTPException(status_code=401, detail="Cloudflare Access identity is not the configured administrator")
        return email

    if mode != "bearer":
        raise HTTPException(status_code=503, detail="Unsupported authentication mode")
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
