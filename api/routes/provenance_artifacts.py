"""#310: artifact lineage read over the :Artifact / DERIVED_FROM graph.

Cross-run lineage the provenance spine could not express before: given a content
hash, walk the DERIVED_FROM chain to the ancestor artifacts this one was derived
from, each annotated with the decision that PRODUCED it. Org-scoped to the
caller (mirrors provenance_lineage.py), so a tenant sees only its own artifacts.
"""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.dependencies import resolve_organization_id
from api.routes.provenance import get_kg  # reuse the module's KG accessor

router = APIRouter(prefix="/api/v1", tags=["provenance"])


class ArtifactLineageQuery(BaseModel):
    sha256: str = Field(..., description="Content hash, e.g. 'sha256:<64 hex>'.")
    limit: int = Field(50, ge=1, le=500)


class ArtifactAncestor(BaseModel):
    sha256: str
    uri: Optional[str] = None
    depth: int
    produced_by: Optional[dict] = None  # the decision that PRODUCED this ancestor


class ArtifactLineageResult(BaseModel):
    sha256: str
    found: bool  # whether the artifact exists in the caller's org
    ancestors: list[ArtifactAncestor]
    count: int


# One query: MATCH the base artifact (org-scoped), OPTIONAL-MATCH the transitive
# DERIVED_FROM ancestors and each ancestor's producing decision. The base row is
# always returned (anc null when there are no ancestors) so `found` is knowable;
# a missing artifact matches nothing → zero rows → found=false.
_LINEAGE = (
    "MATCH (a:Artifact {sha256: $sha256}) "
    "WHERE ($organization_id IS NULL OR a.organization_id = $organization_id) "
    "OPTIONAL MATCH p = (a)-[:DERIVED_FROM*1..]->(anc:Artifact) "
    "  WHERE anc.organization_id = a.organization_id "
    "OPTIONAL MATCH (pd:Decision)-[:PRODUCED]->(anc) "
    "WITH a, anc, min(length(p)) AS depth, "
    "     head(collect(pd {.id, .action_type, .summary, .occurred_at})) AS produced_by "
    "RETURN a.sha256 AS base_sha256, anc.sha256 AS sha256, anc.uri AS uri, "
    "       depth, produced_by "
    "ORDER BY depth "
    "LIMIT $limit"
)


@router.post("/provenance/artifact-lineage", response_model=ArtifactLineageResult)
async def artifact_lineage(
    q: ArtifactLineageQuery,
    organization_id: str = Depends(resolve_organization_id),
) -> ArtifactLineageResult:
    kg = get_kg()
    rows = kg.execute_cypher(
        _LINEAGE,
        {"sha256": q.sha256, "limit": q.limit, "organization_id": organization_id},
    )
    found = len(rows) > 0
    ancestors = [
        ArtifactAncestor(sha256=r["sha256"], uri=r.get("uri"), depth=r["depth"],
                         produced_by=r.get("produced_by"))
        for r in rows
        if r.get("sha256") is not None
    ]
    return ArtifactLineageResult(
        sha256=q.sha256, found=found, ancestors=ancestors, count=len(ancestors),
    )
