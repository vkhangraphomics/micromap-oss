"""
API Dependencies for MicroMap

Provides FastAPI dependencies for authentication and authorization.
"""

import os
from fastapi import Header, HTTPException, status

from api import identity


def _bearer(authorization: str | None) -> str | None:
    """The raw token from an `Authorization: Bearer <token>` header, or None.

    Guards with `isinstance(str)` so it tolerates being called directly with the
    FastAPI `Header(None)` default sentinel (these deps are unit-tested by direct
    invocation, not only via request injection).
    """
    if isinstance(authorization, str) and authorization.startswith("Bearer "):
        return authorization[len("Bearer "):]
    return None


def get_valid_api_keys() -> set:
    """
    Get the set of valid API keys from environment.

    Returns empty set if no keys configured (allows all requests in dev mode).
    """
    keys = os.environ.get("API_KEYS", "")
    return set(k.strip() for k in keys.split(",") if k.strip())


async def verify_api_key(
    x_api_key: str = Header(
        None,
        alias="X-API-Key",
        description="API key for authentication"
    ),
    authorization: str = Header(None),
) -> str:
    """
    Verify the API key from request header.

    Accepts either X-API-Key header or Authorization: Bearer <token> header.

    Args:
        x_api_key: API key from X-API-Key header
        authorization: Authorization header with optional Bearer token

    Returns:
        The validated API key

    Raises:
        HTTPException: 401 if key is invalid or missing
    """
    bearer = _bearer(authorization)

    # JWT path (#190 pillar 5): a valid Bearer JWT authenticates on its own,
    # regardless of API_KEYS. Disabled/invalid/not-a-JWT -> fall through to the
    # API-key path below (so plain Bearer-as-key keeps working unchanged).
    if bearer and identity.jwt_enabled() and identity.verify_jwt(bearer) is not None:
        return bearer

    api_key = x_api_key or bearer

    valid_keys = get_valid_api_keys()

    # If no keys configured, allow all requests (dev mode)
    if not valid_keys:
        if not api_key:
            api_key = "dev-mode"
        return api_key

    if not api_key or api_key not in valid_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    return api_key


def get_api_key_orgs() -> dict:
    """Map API key -> organization_id from the env.

    `API_KEY_ORGS` is a comma-separated list of `key:org` pairs, e.g.
    `key_abc:acme,key_def:graphomics`. When set, the caller's organization is
    derived from their authenticated key — NOT trusted from the request body —
    which is what makes org-scoping a real boundary instead of a filter.
    """
    raw = os.environ.get("API_KEY_ORGS", "")
    out: dict = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        key, org = pair.split(":", 1)
        key, org = key.strip(), org.strip()
        if key and org:
            out[key] = org
    return out


async def resolve_organization_id(
    x_api_key: str = Header(None, alias="X-API-Key"),
    authorization: str = Header(None),
) -> str:
    """Resolve the caller's organization_id from their API key or Bearer token.

    Precedence:
    1. X-API-Key header (if provided)
    2. Authorization Bearer token (if X-API-Key is absent)
    3. API_KEY_ORGS mapping (key -> org)
    4. DEFAULT_ORG (default 'default')

    Provenance writes/reads use THIS value, so a caller can only ever write to /
    read from their own org — the request body cannot widen scope.

    JWT (#190 pillar 5): when JWT is enabled and the Bearer token is a valid JWT,
    org comes from its `org_id` claim (the unified-identity path). Otherwise the
    existing API-key resolution applies, so nothing changes when JWT is unset.
    """
    bearer = _bearer(authorization)

    if bearer and identity.jwt_enabled():
        claims = identity.verify_jwt(bearer)
        if claims is not None:
            org = identity.org_from_claims(claims)
            if org:
                return org

    api_key = x_api_key or bearer
    if not api_key:
        return os.environ.get("DEFAULT_ORG", "default")

    return get_api_key_orgs().get(api_key) or os.environ.get("DEFAULT_ORG", "default")


async def resolve_principal(
    x_api_key: str = Header(None, alias="X-API-Key"),
    authorization: str = Header(None),
) -> identity.Principal:
    """Resolve the full caller identity (#190 pillar 5): org + user + role + how.

    A valid Bearer JWT yields a `jwt` principal (service accounts carry
    `role=service`). Otherwise an `api_key` principal whose org is resolved from
    the key map — role/user are unknown for opaque keys. Org is always the
    authoritative scoping value; never trusted from the request body.
    """
    bearer = _bearer(authorization)

    if bearer and identity.jwt_enabled():
        claims = identity.verify_jwt(bearer)
        if claims is not None:
            principal = identity.principal_from_claims(claims)
            if principal.org_id:
                return principal

    api_key = x_api_key or bearer
    default_org = os.environ.get("DEFAULT_ORG", "default")
    org = (get_api_key_orgs().get(api_key) or default_org) if api_key else default_org
    return identity.Principal(org_id=org, auth="api_key")


async def optional_api_key(
    x_api_key: str = Header(
        None,
        alias="X-API-Key",
        description="Optional API key for authentication"
    )
) -> str | None:
    """
    Optional API key validation - for endpoints that work with or without auth.

    Returns the key if provided and valid, None otherwise.
    """
    if x_api_key is None:
        return None

    valid_keys = get_valid_api_keys()

    if not valid_keys:
        return x_api_key

    if x_api_key in valid_keys:
        return x_api_key

    return None
