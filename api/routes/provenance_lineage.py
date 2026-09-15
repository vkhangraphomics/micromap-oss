"""GPV-254: lineage read over the provenance spine (current + as-of)."""
from typing import Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.dependencies import resolve_organization_id
from api.routes.provenance import get_kg  # reuse the module's KG accessor

router = APIRouter(prefix="/api/v1", tags=["provenance"])


class LineageQuery(BaseModel):
    entity: str = Field(..., description="Disease or taxon name to trace")
    as_of: Optional[str] = Field(None, description="ISO datetime; omit for current truth")
    limit: int = Field(50, ge=1, le=500)


class LineagePath(BaseModel):
    experiment: Optional[dict] = None
    analysis: dict
    assertion: dict
    subject: str
    object: str
    decisions: list[dict] = []


class LineageResult(BaseModel):
    entity: str
    as_of: Optional[str] = None
    paths: list[LineagePath]
    count: int


_BASE = (
    "MATCH (a:Assertion)-[:SUBJECT|OBJECT]->(e) "
    "WHERE (toLower(e.name) = toLower($entity) OR e.name_normalized = toLower($entity)) "
    "AND ($organization_id IS NULL OR a.organization_id = $organization_id) "
)
_CURRENT_FILTER = "AND NOT (a)-[:SUPERSEDED_BY]->() "
_ASOF_FILTER = (
    "AND a.valid_from <= $as_of AND (a.valid_to IS NULL OR a.valid_to > $as_of) "
    "AND NOT EXISTS { MATCH (a)-[:SUPERSEDED_BY]->(n:Assertion) WHERE n.valid_from <= $as_of } "
)
_TAIL = (
    "MATCH (an:Analysis)-[:ASSERTED]->(a) "
    "OPTIONAL MATCH (ex:Experiment)-[:HAS_ANALYSIS]->(an) "
    "MATCH (a)-[:SUBJECT]->(s), (a)-[:OBJECT]->(o) "
    "OPTIONAL MATCH (an)-[:RECORDED]->(d:Decision) "
    "WITH ex, an, a, s, o, "
    "     collect(DISTINCT d {.id, .action_type, .decision_outcome, .summary, .occurred_at}) AS decisions "
    "RETURN ex {.*} AS experiment, an {.*} AS analysis, a {.*} AS assertion, "
    "       s.name AS subject, o.name AS object, decisions "
    "ORDER BY a.asserted_at DESC LIMIT $limit"
)


@router.post("/provenance/lineage", response_model=LineageResult)
async def provenance_lineage(
    q: LineageQuery,
    organization_id: str = Depends(resolve_organization_id),
):
    kg = get_kg()
    params = {"entity": q.entity, "limit": q.limit, "organization_id": organization_id}
    if q.as_of:
        cypher = _BASE + _ASOF_FILTER + "WITH DISTINCT a " + _TAIL
        params["as_of"] = q.as_of
    else:
        cypher = _BASE + _CURRENT_FILTER + "WITH DISTINCT a " + _TAIL
    rows = kg.execute_cypher(cypher, params)
    paths = [LineagePath(experiment=r.get("experiment"), analysis=r["analysis"],
                         assertion=r["assertion"], subject=r["subject"], object=r["object"],
                         decisions=r.get("decisions") or [])
             for r in rows]
    return LineageResult(entity=q.entity, as_of=q.as_of, paths=paths, count=len(paths))
