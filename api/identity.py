"""JWT + service-account identity for the API (#190 pillar 5).

Config-gated: when no verification material is configured, JWT is **disabled**
and the auth layer behaves exactly as before (API-key / opaque-Bearer). When
configured, a valid ``Authorization: Bearer <JWT>`` authenticates and its claims
drive org-scoping + role. Service accounts are JWTs carrying ``role=service``
(validate-only — the host trusts tokens minted by the configured issuer).

Verification material (pick one):
  - ``JWT_PUBLIC_KEY``  PEM public key   -> RS256 (asymmetric; issuer signs)
  - ``JWKS_URL``        JWKS endpoint    -> RS256 (key selected by ``kid``)
  - ``JWT_SECRET``      shared secret    -> HS256 (simple / dev)

Optional: ``JWT_ISSUER`` (validate ``iss``), ``JWT_AUDIENCE`` (validate ``aud``).
Claim names are configurable: ``JWT_ORG_CLAIM`` (default ``org_id``),
``JWT_USER_CLAIM`` (default ``sub``), ``JWT_ROLE_CLAIM`` (default ``role``).
The role claim may be a string or a list (``roles_from_claims``).

Workbench OIDC rollout (GPV-410) — set on the deployed env to turn this on::

    JWKS_URL=<workbench>/oauth2/jwks
    JWT_AUDIENCE=micromap
    JWT_ISSUER=<workbench issuer>
    JWT_ORG_CLAIM=orgId
    JWT_ROLE_CLAIM=roles

``API_KEYS`` keep working alongside JWTs (no flag-day). The MCP server mirrors
this logic in ``micromap-mcp/micromap_mcp/auth.py`` — keep the two in sync.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

import jwt


@dataclass(frozen=True)
class Principal:
    """The authenticated caller: org (the scoping boundary) + who/role + how."""
    org_id: str
    user_id: str = ""
    role: str = ""
    auth: str = "api_key"  # "jwt" | "api_key"

    @property
    def is_service(self) -> bool:
        return self.role.strip().lower() == "service"


def _secret() -> Optional[str]:
    return os.environ.get("JWT_SECRET") or None


def _public_key() -> Optional[str]:
    return os.environ.get("JWT_PUBLIC_KEY") or None


def _jwks_url() -> Optional[str]:
    return os.environ.get("JWKS_URL") or None


def jwt_enabled() -> bool:
    """True when any JWT verification material is configured."""
    return bool(_secret() or _public_key() or _jwks_url())


def verify_jwt(token: str) -> Optional[dict[str, Any]]:
    """Validate a JWT (signature + exp + optional iss/aud).

    Returns the claims dict, or ``None`` when the token is invalid/expired, isn't
    a JWT at all (e.g. a plain API key in the Bearer slot), or JWT is disabled.
    Never raises — callers fall back to the API-key path on ``None``.
    """
    if not token or not jwt_enabled():
        return None

    issuer = os.environ.get("JWT_ISSUER") or None
    audience = os.environ.get("JWT_AUDIENCE") or None
    kwargs: dict[str, Any] = {"options": {"verify_aud": bool(audience)}}
    if audience:
        kwargs["audience"] = audience
    if issuer:
        kwargs["issuer"] = issuer

    try:
        secret = _secret()
        if secret:
            return jwt.decode(token, secret, algorithms=["HS256"], **kwargs)
        pub = _public_key()
        if pub:
            return jwt.decode(token, pub, algorithms=["RS256"], **kwargs)
        jwks = _jwks_url()
        if jwks:
            signing_key = jwt.PyJWKClient(jwks).get_signing_key_from_jwt(token).key
            return jwt.decode(token, signing_key, algorithms=["RS256"], **kwargs)
    except jwt.InvalidTokenError:
        return None
    except Exception:  # JWKS fetch / network / malformed key material
        return None
    return None


def org_from_claims(claims: dict) -> str:
    return str(claims.get(os.environ.get("JWT_ORG_CLAIM", "org_id")) or "")


def roles_from_claims(claims: dict) -> list[str]:
    """Normalize the role claim to a list of role strings.

    The role claim may be a single string or a JSON list — Workbench emits
    ``roles`` as a list (set ``JWT_ROLE_CLAIM=roles`` at rollout). Empty values
    are dropped. The claim NAME defaults to ``role`` to preserve existing REST
    behavior; rollout aliases it to ``roles`` via env.
    """
    raw = claims.get(os.environ.get("JWT_ROLE_CLAIM", "role"))
    if isinstance(raw, list):
        return [s for r in raw if (s := str(r))]
    if raw:
        return [str(raw)]
    return []


def principal_from_claims(claims: dict) -> Principal:
    roles = roles_from_claims(claims)
    return Principal(
        org_id=org_from_claims(claims),
        user_id=str(claims.get(os.environ.get("JWT_USER_CLAIM", "sub")) or ""),
        role=roles[0] if roles else "",
        auth="jwt",
    )
