"""Workbench OIDC JWT verification for the MCP server (GPV-410 / #232).

Self-contained mirror of api/identity.py — micromap-mcp is a standalone
package (it does not depend on api/), so the ~30-line verify logic is
duplicated here intentionally; keep the two in sync.

Config-gated: with no JWT material configured, jwt_enabled() is False and
verify_workbench_jwt() returns None (the server then relies on the static
bearer). Verification material precedence: JWT_SECRET (HS256) ->
JWT_PUBLIC_KEY (RS256 PEM) -> JWKS_URL (RS256 via JWKS). Optional JWT_ISSUER
/ JWT_AUDIENCE. Claim names default to Workbench's orgId/sub/roles.
"""
from __future__ import annotations

import hmac
import os
from dataclasses import dataclass
from typing import Any, Optional

import jwt
from fastmcp.server.auth import TokenVerifier, AccessToken
from fastmcp.server.dependencies import get_access_token


@dataclass(frozen=True)
class Principal:
    org_id: str
    user_id: str = ""
    role: str = ""
    auth: str = "static"  # "jwt" | "static"

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
    return bool(_secret() or _public_key() or _jwks_url())


# Roles permitted to run arbitrary Cypher (#299). First-party service callers
# (Nexus) and admins only; everyone else uses the structured kg_* tools.
PRIVILEGED_ROLES = frozenset({"service", "admin"})


def _parse_colon_entries(raw: str) -> list[list[str]]:
    """Parse a colon-separated entry list with validation.

    Format: ``key:value:optional``, comma-separated entries. Each entry is
    split on ``:``, stripped, and validated to have at least a non-empty key
    and value. Malformed entries are skipped silently.
    """
    out: list[list[str]] = []
    for entry in raw.split(","):
        parts = [p.strip() for p in entry.strip().split(":")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        out.append(parts)
    return out


def token_orgs() -> dict[str, tuple[str, str]]:
    """Map static bearer token -> (org_id, role) from ``MCP_TOKEN_ORGS``.

    Format ``token:org:role``, comma-separated; role optional. This is the MCP
    mirror of REST's ``API_KEY_ORGS`` (api/dependencies.py) — keep the two in
    sync. This is what makes a static-token caller carry an org at all — the
    premise the old "session-trust posture" rested on was simply that this map
    did not exist.

    Malformed entries are skipped rather than raising: a typo must not take the
    server down, and must not silently grant an org either.
    """
    raw = os.environ.get("MCP_TOKEN_ORGS", "")
    out: dict[str, tuple[str, str]] = {}
    for parts in _parse_colon_entries(raw):
        out[parts[0]] = (parts[1], parts[2] if len(parts) > 2 else "")
    return out


def org_api_keys() -> dict[str, str]:
    """Map org -> outbound REST API key from ``MCP_ORG_API_KEYS``.

    Used by the kg_* tools so a REST call carries the *caller's* credential;
    REST then derives the org itself via API_KEY_ORGS. The MCP never asserts an
    org to REST — it only chooses which credential to present.
    """
    raw = os.environ.get("MCP_ORG_API_KEYS", "")
    out: dict[str, str] = {}
    for parts in _parse_colon_entries(raw):
        out[parts[0]] = parts[1]
    return out


def default_org() -> str:
    """Org for a caller with no mapped identity.

    Mirrors REST's DEFAULT_ORG (api/dependencies.py) — keep the two in sync.

    ``os.environ.get(key, fallback)`` only falls back when the key is
    *absent* — an operator who explicitly sets ``MCP_DEFAULT_ORG=""`` (or to
    whitespace) would otherwise get that back verbatim. Every write path in
    this package uses ``... or default_org()`` as its never-empty guarantee
    (#299), so this must never return a falsy value.
    """
    return os.environ.get("MCP_DEFAULT_ORG", "default").strip() or "default"


def principal_org() -> str:
    """Org for the in-flight request's authenticated principal, never empty.

    ``(p.org_id if p else "") or default_org()`` — the never-empty write-path
    derivation used across the package — was spelled out inline at three call
    sites (`tools/mapforge.py`, `tools/provenance.py` twice), which is exactly
    the kind of triplicated logic that drifts out of sync unnoticed (#299
    final review, item 6). This is the single definition; every write path
    should call this instead of re-deriving it.

    Deliberately distinct from `scoping.OrgScope`/`ORG_FILTER`: those express
    a *read* scope (own org OR public/canonical orgs); this is just "the org
    a write gets stamped with," which is always exactly one org.
    """
    p = current_principal()
    return (p.org_id if p else "") or default_org()


def identity_configured() -> bool:
    """True when the server can tell callers apart.

    Gates enforcement (#299): with one shared token and no map there is no way
    to distinguish first-party from external, so enforcing would refuse every
    live caller. Deliberately NOT a separate feature flag — tying it to the
    thing it depends on means the switch cannot drift out of sync.
    """
    return bool(token_orgs()) or jwt_enabled()


def match_static_token(token: str, legacy_token: Optional[str]) -> Optional[tuple[str, str]]:
    """Return ``(org, role)`` if ``token`` is a known static token, else None.

    Uses ``hmac.compare_digest`` for all comparisons (never ==) to avoid
    leaking a match position through timing. Returns on first match.
    """
    for known, (org, role) in token_orgs().items():
        if hmac.compare_digest(token, known):
            # token_orgs() always stores truthy orgs, so this is always org (never falls through)
            return (org, role)
    if legacy_token and hmac.compare_digest(token, legacy_token):
        return (default_org(), "")
    return None


def verify_workbench_jwt(token: str) -> Optional[dict[str, Any]]:
    """Validate a JWT (signature + exp + optional iss/aud). Never raises;
    returns claims or None (invalid/expired/non-JWT/disabled)."""
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


def roles_from_claims(claims: dict) -> list[str]:
    raw = claims.get(os.environ.get("JWT_ROLE_CLAIM", "roles"))
    if isinstance(raw, list):
        return [s for r in raw if (s := str(r))]
    if raw:
        return [str(raw)]
    return []


def principal_from_claims(claims: dict) -> Principal:
    roles = roles_from_claims(claims)
    return Principal(
        org_id=str(claims.get(os.environ.get("JWT_ORG_CLAIM", "orgId")) or ""),
        user_id=str(claims.get(os.environ.get("JWT_USER_CLAIM", "sub")) or ""),
        role=roles[0] if roles else "",
        auth="jwt",
    )


class WorkbenchJWKSVerifier(TokenVerifier):
    """Verify a Workbench OIDC JWT (via JWKS/PEM/secret); fall back to a
    single static bearer during migration. Dual-auth: both work when
    configured. `static_token=None` disables the fallback."""

    def __init__(self, static_token: Optional[str]) -> None:
        super().__init__()
        self._static_token = static_token

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        claims = verify_workbench_jwt(token)
        if claims is not None:
            p = principal_from_claims(claims)
            return AccessToken(
                token=token,
                client_id=p.user_id or "workbench",
                scopes=roles_from_claims(claims),
                expires_at=claims.get("exp"),
                claims={
                    "org_id": p.org_id,
                    "user_id": p.user_id,
                    "role": p.role,
                    "auth": "jwt",
                },
            )
        matched = match_static_token(token, self._static_token)
        if matched is not None:
            org, role = matched
            return AccessToken(
                token=token,
                client_id="nexus",
                scopes=[role] if role else [],
                claims={
                    "org_id": org,
                    "user_id": "",
                    "role": role,
                    "auth": "static",
                },
            )
        return None


def current_principal() -> Optional[Principal]:
    """Principal for the in-flight MCP request, or None if unauthenticated."""
    at = get_access_token()
    if at is None:
        return None
    claims = at.claims or {}
    return Principal(
        org_id=str(claims.get("org_id") or ""),
        user_id=str(claims.get("user_id") or ""),
        role=str(claims.get("role") or ""),
        auth=str(claims.get("auth") or "static"),
    )
