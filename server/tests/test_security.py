import os
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from app import security


def test_bearer_mode_compatibility(monkeypatch):
    monkeypatch.setenv("OHO_AUTH_MODE", "bearer")
    monkeypatch.setenv("OHO_ADMIN_TOKEN", "admin-secret-" + "a" * 32)
    assert security.admin("Bearer " + os.environ.get("OHO_ADMIN_TOKEN", ""), "") == "admin"
    with pytest.raises(HTTPException) as err:
        security.admin("Bearer wrong", "")
    assert err.value.status_code == 401


def test_cloudflare_access_requires_signed_assertion(monkeypatch):
    monkeypatch.setenv("OHO_AUTH_MODE", "cloudflare_access")
    monkeypatch.setenv("OHO_ACCESS_ISSUER", "https://team.cloudflareaccess.com")
    monkeypatch.setenv("OHO_ACCESS_AUD", "oho-audience")
    monkeypatch.setenv("OHO_ADMIN_EMAIL", "owner@example.com")
    with pytest.raises(HTTPException) as err:
        security.admin("", "")
    assert err.value.status_code == 401


def test_cloudflare_access_checks_exact_email(monkeypatch):
    monkeypatch.setenv("OHO_AUTH_MODE", "cloudflare_access")
    monkeypatch.setenv("OHO_ACCESS_ISSUER", "https://team.cloudflareaccess.com")
    monkeypatch.setenv("OHO_ACCESS_AUD", "oho-audience")
    monkeypatch.setenv("OHO_ADMIN_EMAIL", "owner@example.com")
    monkeypatch.setattr(security, "verify_access_assertion", lambda token: {"email": "owner@example.com"})
    assert security.admin("", "verified-jwt") == "owner@example.com"
    monkeypatch.setattr(security, "verify_access_assertion", lambda token: {"email": "other@example.com"})
    with pytest.raises(HTTPException) as err:
        security.admin("", "verified-jwt")
    assert err.value.status_code == 401


def test_access_mode_config_does_not_require_admin_token(monkeypatch):
    monkeypatch.setenv("OHO_AUTH_MODE", "cloudflare_access")
    monkeypatch.delenv("OHO_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("OHO_ENROLLMENT_TOKEN", "enrollment-" + "e" * 32)
    monkeypatch.setenv("OHO_ACCESS_ISSUER", "https://team.cloudflareaccess.com")
    monkeypatch.setenv("OHO_ACCESS_AUD", "oho-audience")
    monkeypatch.setenv("OHO_ADMIN_EMAIL", "owner@example.com")
    monkeypatch.delenv("OHO_ACCESS_JWKS_URL", raising=False)
    security.require_config()


def test_access_assertion_validates_real_signature_issuer_audience_and_expiry(monkeypatch):
    issuer = "https://team.cloudflareaccess.com"
    audience = "oho-audience"
    monkeypatch.setenv("OHO_ACCESS_ISSUER", issuer)
    monkeypatch.setenv("OHO_ACCESS_AUD", audience)
    monkeypatch.setenv("OHO_ADMIN_EMAIL", "owner@example.com")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    token = jwt.encode({"iss": issuer, "aud": audience, "iat": now,
                        "exp": now + 120, "email": "owner@example.com"},
                       private_key, algorithm="RS256", headers={"kid": "test-key"})
    class FakeJwks:
        def get_signing_key_from_jwt(self, _token):
            return SimpleNamespace(key=private_key.public_key())
    monkeypatch.setattr(security, "_jwks_client", lambda _issuer, _url: FakeJwks())
    assert security.verify_access_assertion(token)["email"] == "owner@example.com"
    bad_aud = jwt.encode({"iss": issuer, "aud": "wrong-audience", "iat": now,
                          "exp": now + 120, "email": "owner@example.com"},
                         private_key, algorithm="RS256", headers={"kid": "test-key"})
    with pytest.raises(jwt.InvalidAudienceError):
        security.verify_access_assertion(bad_aud)
