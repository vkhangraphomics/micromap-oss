"""Org-scope policy for the public read API (#200).

A node is visible to a caller iff it belongs to the caller's own org OR to a
public/shared org (the reference KG — taxonomy, diseases, etc., all stamped
`organization_id = "default"`). The caller's org is derived from their API key
via `resolve_organization_id`, never trusted from the request body, so a client
cannot widen its own scope.
"""

import os
from dataclasses import dataclass
from typing import List, Optional

from fastapi import Header

from api.dependencies import resolve_organization_id


def get_public_orgs() -> List[str]:
    """Orgs whose data is visible to every caller (shared reference KG).

    `PUBLIC_ORGS` is a comma-separated env list; defaults to `default`, which is
    what every loader stamps (`load_knowledge_graph.DEFAULT_ORG_ID`).
    """
    raw = os.environ.get("PUBLIC_ORGS", "default")
    orgs = [o.strip() for o in raw.split(",") if o.strip()]
    return orgs or ["default"]


@dataclass
class OrgScope:
    """The caller's read scope: their own org plus the shared/public orgs."""
    org_id: str
    public_orgs: List[str]


async def resolve_org_scope(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None),
) -> OrgScope:
    """FastAPI dependency: derive the caller's OrgScope from their API key."""
    org_id = await resolve_organization_id(x_api_key=x_api_key, authorization=authorization)
    return OrgScope(org_id=org_id, public_orgs=get_public_orgs())


def ORG_FILTER(var: str) -> str:
    """Cypher predicate that scopes `var` to the caller's org + public orgs."""
    return (
        f"({var}.organization_id = $organization_id "
        f"OR {var}.organization_id IN $public_orgs)"
    )


def EDGE_ORG_FILTER(var: str) -> str:
    """Cypher predicate scoping a RELATIONSHIP `var` to the caller (#297).

    Same shape as `ORG_FILTER` but **null-tolerant**: a relationship with no
    `organization_id` is a shared/ingested reference edge (GMRepo/Disbiome/…,
    which never stamp edge org) and must stay visible to every caller, so the
    `IS NULL` branch is required for back-compat. An org-stamped edge is visible
    only to its own org (+ public/canonical orgs) — which is what keeps a
    customer's contributed edge from leaking to every caller (the #284 leak in
    edge form). Reuses the same `$organization_id`/`$public_orgs` params as
    `ORG_FILTER`, so `scope_params()` already feeds it — no new plumbing.
    """
    return (
        f"({var}.organization_id IS NULL "
        f"OR {var}.organization_id = $organization_id "
        f"OR {var}.organization_id IN $public_orgs)"
    )


def scope_params(scope: OrgScope) -> dict:
    """Params to merge into every scoped query."""
    return {"organization_id": scope.org_id, "public_orgs": scope.public_orgs}
