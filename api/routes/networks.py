"""
Network-related API routes.

Cross-feeding is **not available** in the knowledge graph (#305).

These endpoints matched `(:Taxon)-[:CROSS_FEEDS]->(:Taxon)`. No loader has ever
written `CROSS_FEEDS`, and the only Taxon-to-Taxon edge in the graph is
`HAS_PARENT` (811,720 edges, the taxonomic hierarchy) — so there is no
cross-feeding edge of any kind to read, and none to re-point the queries at.

Because the patterns were plain rather than optional, both endpoints returned
zero rows for every request while answering `200`. An empty network reads as a
finding — "this taxon has no cross-feeding partners" — which is a claim about
the biology. We hold no data either way, so the endpoints now say so. Same
reasoning as #304 (`/genes/{id}/taxa`).

The former query bodies built cross-feeding edges via a metabolite intermediary
and split partners by direction. They are preserved in git history (the commit
referencing #305) rather than kept here as unreachable code that invites
someone to "re-enable" a relationship that does not exist. Serving this needs a
real cross-feeding ingest first.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from api.scoping import OrgScope, resolve_org_scope

router = APIRouter()

_UNAVAILABLE = (
    "Cross-feeding data is not available: no cross-feeding relationship is "
    "loaded in the knowledge graph. The only Taxon-to-Taxon edge is HAS_PARENT "
    "(taxonomic hierarchy). See issue #305."
)


@router.get("/networks/cross-feeding")
async def get_cross_feeding_network(
    taxon_id: Optional[str] = Query(None, description="Filter to a specific taxon (by taxon_id or NCBITaxon: prefixed ID)"),
    metabolite: Optional[str] = Query(None, description="Filter by metabolite name (case-insensitive partial match)"),
    limit: int = Query(100, ge=1, le=500, description="Maximum number of edges to return"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Not available: the graph holds no cross-feeding relationships.

    Previously returned an empty `edges`/`nodes` network with a `200` for every
    request, which asserts that no cross-feeding exists rather than that we
    cannot answer. See the module docstring and #305.
    """
    raise HTTPException(status_code=501, detail=_UNAVAILABLE)


@router.get("/networks/cross-feeding/{taxon_id}")
async def get_cross_feeding_partners(
    taxon_id: str,
    limit: int = Query(100, ge=1, le=500, description="Maximum number of partners to return"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Not available: the graph holds no cross-feeding relationships.

    Previously returned empty `producers_for`/`consumers_of` lists with a `200`,
    which reads as "this taxon has no cross-feeding partners". See #305.
    """
    raise HTTPException(status_code=501, detail=_UNAVAILABLE)
