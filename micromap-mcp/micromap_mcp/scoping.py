"""Org-scope policy for the MCP surface (#299).

Self-contained mirror of ``api/scoping.py`` — micromap-mcp is a standalone
package and does not depend on ``api/`` — so the predicate is duplicated here
intentionally; keep the two in sync.

A node is visible to a caller iff it belongs to the caller's own org OR to a
public org (the shared reference KG, all stamped ``organization_id="default"``).
The caller's org comes from their credential via ``auth.current_principal()``,
never from a tool argument, so a caller cannot widen their own scope.

``api/scoping.py`` additionally has ``get_public_orgs()`` and a
``resolve_org_scope`` dependency that derives an ``OrgScope`` straight from
``PUBLIC_ORGS`` for every REST route. This module deliberately does **not**
carry that pair (né ``public_orgs()`` / ``scope_for()``, removed #299 final
review item 6): the one production consumer that builds an ``OrgScope`` here,
``tools/provenance.py``'s ``provenance_lineage``, needs the *canonical* orgs
(``micromap_mapforge.provenance.spine.canonical_orgs()``) as its public set,
not the generic ``PUBLIC_ORGS``-derived list — see that module's docstring.
Re-add them only if a real caller needs the generic-``PUBLIC_ORGS`` shape;
until then it's dead code with no test but its own.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OrgScope:
    """The caller's read scope: their own org plus the public orgs."""
    org_id: str
    public_orgs: list[str]


def ORG_FILTER(var: str) -> str:
    """Cypher predicate scoping ``var`` to the caller's org + public orgs.

    Only the variable NAME is interpolated. The org values always arrive as
    bound parameters, so there is no string surface for a caller to attack.
    """
    return (
        f"({var}.organization_id = $organization_id "
        f"OR {var}.organization_id IN $public_orgs)"
    )


def scope_params(scope: OrgScope) -> dict:
    """Params to merge into every scoped query."""
    return {"organization_id": scope.org_id, "public_orgs": list(scope.public_orgs)}
