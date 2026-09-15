"""JWT + service-account identity (#190 pillar 5).

Unit tests for `api.identity` (HS256 + RS256 verification, claims, Principal)
and the JWT-aware `api.dependencies` auth functions, including backward
compatibility with the existing API-key path.
"""
import asyncio
import time

import jwt
import pytest

from api import dependencies, identity

_SECRET = "test-secret"


def _hs(claims: dict, *, secret: str = _SECRET, exp_in: int = 3600) -> str:
    return jwt.encode(
        {"exp": int(time.time()) + exp_in, **claims}, secret, algorithm="HS256"
    )


def _rsa_keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return priv, pub


@pytest.fixture
def hs_enabled(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", _SECRET)
    for k in ("JWT_PUBLIC_KEY", "JWKS_URL", "JWT_ISSUER", "JWT_AUDIENCE",
              "JWT_ORG_CLAIM", "JWT_USER_CLAIM", "JWT_ROLE_CLAIM"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def jwt_disabled(monkeypatch):
    for k in ("JWT_SECRET", "JWT_PUBLIC_KEY", "JWKS_URL"):
        monkeypatch.delenv(k, raising=False)


# --- identity unit ---------------------------------------------------------

def test_disabled_by_default(jwt_disabled):
    assert identity.jwt_enabled() is False
    assert identity.verify_jwt(_hs({"org_id": "acme"})) is None


def test_hs256_valid_and_failures(hs_enabled):
    claims = identity.verify_jwt(_hs({"org_id": "acme", "sub": "u1", "role": "scientist"}))
    assert claims and claims["org_id"] == "acme"
    assert identity.verify_jwt(_hs({"org_id": "acme"}, secret="wrong")) is None   # bad sig
    assert identity.verify_jwt(_hs({"org_id": "acme"}, exp_in=-5)) is None         # expired
    assert identity.verify_jwt("mm_plain_api_key") is None                         # not a JWT


def test_hs256_issuer_and_audience(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", _SECRET)
    monkeypatch.setenv("JWT_ISSUER", "nexus")
    monkeypatch.setenv("JWT_AUDIENCE", "micromap")
    base = {"org_id": "acme", "exp": int(time.time()) + 60}
    good = jwt.encode({**base, "iss": "nexus", "aud": "micromap"}, _SECRET, algorithm="HS256")
    assert identity.verify_jwt(good)["org_id"] == "acme"
    assert identity.verify_jwt(
        jwt.encode({**base, "iss": "nexus", "aud": "other"}, _SECRET, algorithm="HS256")) is None
    assert identity.verify_jwt(
        jwt.encode({**base, "iss": "evil", "aud": "micromap"}, _SECRET, algorithm="HS256")) is None


def test_rs256_valid_and_wrong_key(monkeypatch, jwt_disabled):
    priv, pub = _rsa_keypair()
    monkeypatch.setenv("JWT_PUBLIC_KEY", pub)
    token = jwt.encode({"org_id": "acme", "exp": int(time.time()) + 60}, priv, algorithm="RS256")
    assert identity.verify_jwt(token)["org_id"] == "acme"
    other_priv, _ = _rsa_keypair()
    bad = jwt.encode({"org_id": "acme", "exp": int(time.time()) + 60}, other_priv, algorithm="RS256")
    assert identity.verify_jwt(bad) is None


def test_principal_and_custom_claims(monkeypatch, hs_enabled):
    p = identity.principal_from_claims({"org_id": "acme", "sub": "svc-1", "role": "service"})
    assert (p.org_id, p.user_id, p.role, p.auth) == ("acme", "svc-1", "service", "jwt")
    assert p.is_service is True
    monkeypatch.setenv("JWT_ORG_CLAIM", "tenant")
    monkeypatch.setenv("JWT_ROLE_CLAIM", "scope")
    assert identity.org_from_claims({"tenant": "globex"}) == "globex"
    assert identity.principal_from_claims({"tenant": "globex", "scope": "service"}).is_service


def test_roles_from_claims_list_or_string(monkeypatch, hs_enabled):
    # Default claim name is "role"; value may be a scalar or a list.
    assert identity.roles_from_claims({"role": "service"}) == ["service"]
    assert identity.roles_from_claims({"role": ["admin", "member"]}) == ["admin", "member"]
    assert identity.roles_from_claims({}) == []
    assert identity.roles_from_claims({"role": ["", "x"]}) == ["x"]  # drop empties
    # Honors JWT_ROLE_CLAIM (Workbench emits "roles" as a list).
    monkeypatch.setenv("JWT_ROLE_CLAIM", "roles")
    assert identity.roles_from_claims({"roles": ["svc"]}) == ["svc"]


def test_principal_role_from_workbench_roles_list(monkeypatch, hs_enabled):
    # Workbench rollout: JWT_ROLE_CLAIM=roles, value is a JSON list.
    monkeypatch.setenv("JWT_ROLE_CLAIM", "roles")
    p = identity.principal_from_claims({"org_id": "acme", "sub": "u1", "roles": ["admin", "member"]})
    assert (p.org_id, p.user_id, p.role) == ("acme", "u1", "admin")
    assert p.is_service is False
    svc = identity.principal_from_claims({"org_id": "acme", "roles": ["service"]})
    assert svc.is_service is True
    # No roles claim -> empty role, not service.
    none = identity.principal_from_claims({"org_id": "acme"})
    assert none.role == "" and none.is_service is False


def test_resolve_principal_service_account_roles_list(monkeypatch, hs_enabled):
    monkeypatch.setenv("JWT_ROLE_CLAIM", "roles")
    tok = _hs({"org_id": "acme", "sub": "svc-7", "roles": ["service"]})
    p = asyncio.run(dependencies.resolve_principal(x_api_key=None, authorization=f"Bearer {tok}"))
    assert p.org_id == "acme" and p.is_service and p.auth == "jwt"


# --- dependency integration ------------------------------------------------

def test_resolve_org_from_jwt(hs_enabled):
    org = asyncio.run(dependencies.resolve_organization_id(
        x_api_key=None, authorization=f"Bearer {_hs({'org_id': 'acme'})}"))
    assert org == "acme"


def test_resolve_org_falls_back_to_keymap_when_jwt_disabled(monkeypatch, jwt_disabled):
    monkeypatch.setenv("API_KEY_ORGS", "key_abc:acme")
    org = asyncio.run(dependencies.resolve_organization_id(x_api_key="key_abc", authorization=None))
    assert org == "acme"


def test_verify_api_key_accepts_jwt_even_with_keys_set(monkeypatch, hs_enabled):
    monkeypatch.setenv("API_KEYS", "real_key_1")  # token is NOT a configured key
    tok = _hs({"org_id": "acme"})
    assert asyncio.run(dependencies.verify_api_key(x_api_key=None, authorization=f"Bearer {tok}")) == tok


def test_verify_api_key_rejects_bad_bearer_when_keys_set(monkeypatch, hs_enabled):
    from fastapi import HTTPException
    monkeypatch.setenv("API_KEYS", "real_key_1")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(dependencies.verify_api_key(x_api_key=None, authorization="Bearer not.a.jwt"))
    assert ei.value.status_code == 401


def test_verify_api_key_still_accepts_plain_key(monkeypatch, jwt_disabled):
    monkeypatch.setenv("API_KEYS", "real_key_1")
    assert asyncio.run(
        dependencies.verify_api_key(x_api_key="real_key_1", authorization=None)) == "real_key_1"


def test_resolve_principal_service_account(hs_enabled):
    tok = _hs({"org_id": "acme", "sub": "svc-7", "role": "service"})
    p = asyncio.run(dependencies.resolve_principal(x_api_key=None, authorization=f"Bearer {tok}"))
    assert p.org_id == "acme" and p.is_service and p.auth == "jwt"


def test_resolve_principal_api_key(monkeypatch, jwt_disabled):
    monkeypatch.setenv("API_KEY_ORGS", "key_abc:acme")
    p = asyncio.run(dependencies.resolve_principal(x_api_key="key_abc", authorization=None))
    assert p.org_id == "acme" and p.auth == "api_key" and not p.is_service


def test_deps_tolerate_header_sentinel_default(monkeypatch, jwt_disabled):
    # Calling these deps directly with x_api_key only leaves `authorization` as
    # FastAPI's Header(None) sentinel object (truthy, not a str). _bearer must
    # tolerate it rather than calling .startswith on it (regression: #221).
    monkeypatch.setenv("API_KEY_ORGS", "k1:acme")
    monkeypatch.setenv("API_KEYS", "k1")
    assert asyncio.run(dependencies.resolve_organization_id(x_api_key="k1")) == "acme"
    assert asyncio.run(dependencies.verify_api_key(x_api_key="k1")) == "k1"
    p = asyncio.run(dependencies.resolve_principal(x_api_key="k1"))
    assert p.org_id == "acme" and p.auth == "api_key"
