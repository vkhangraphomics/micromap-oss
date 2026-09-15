"""
Decision Provenance ingest + query routes (#190 demo cut).

A thin layer on top of the existing Neo4j graph: any tool (nexus, workbench,
mapforge) POSTs a DecisionEvent and it MERGEs a `:Decision` node linked by
`:ABOUT` edges to canonical MicroMap entities named in `entity_tags`. Neo4j
*is* the store for the demo (no Postgres event log); auth stays X-API-Key via
the router-level dependency in `api.main`. See
`docs/DECISION_PROVENANCE_DEMO_KICKOFF.md`.

Entity tags are canonical keys, matching `provenance/seed_decisions.cypher`:
  - Disease -> `name_normalized` (e.g. "parkinson disease")
  - Taxon   -> `taxon_id`        (e.g. "NCBITaxon:853")
"""

import base64
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import psycopg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator

from api import decision_log, embeddings
from api.provenance_writes import ARTIFACT_MERGE_CYPHER, DERIVED_FROM_CYPHER
from api.dependencies import resolve_organization_id
from database.ingestion.base_loader import normalize_disease_name
from integrations.neo4j_microbiome import MicrobiomeKG, get_microbiome_kg

router = APIRouter()
logger = logging.getLogger(__name__)


_BARE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _as_of_upper_bound(as_of: str) -> str:
    """Make a bare calendar date an *inclusive* end-of-day bound.

    ``/provenance/as-of`` compares ``d.occurred_at <= $as_of`` lexicographically
    against RFC3339 timestamps. A bare date like ``2026-06-08`` sorts BEFORE any
    same-day timestamp (``2026-06-08T17:00:00Z``), so without this it silently
    drops every decision made on the as-of day itself (PROV-7 off-by-one). Full
    timestamps pass through unchanged.
    """
    return f"{as_of}T23:59:59Z" if _BARE_DATE_RE.match(as_of) else as_of

# Singleton KG instance (mirrors api/routes/provenance.py).
_kg_instance: Optional[MicrobiomeKG] = None


def get_kg() -> MicrobiomeKG:
    """Get or create the MicrobiomeKG instance."""
    global _kg_instance
    if _kg_instance is None:
        _kg_instance = get_microbiome_kg()
    return _kg_instance


def decision_text(summary: str, rationale: str) -> str:
    """The text embedded for semantic search: the decision's what + why."""
    return f"{summary}\n{rationale}".strip()


# --- Keyset pagination (#323) --------------------------------------------
#
# The ledger read endpoints hard-capped at 500/1000 with no cursor, and `count`
# meant rows RETURNED not MATCHING — so the newest N events were the only ones
# retrievable and a caller could not tell it had been truncated. Keyset (not
# offset) pagination over the COMPOSITE (occurred_at, id): a bare occurred_at
# cursor silently drops rows sharing a timestamp, and an outbox/batch drain
# lands many events in the same instant, so `id` is a required tiebreaker.


def _encode_cursor(occurred_at: str, id_: str) -> str:
    """Opaque continuation token over the (occurred_at, id) sort key."""
    raw = json.dumps([occurred_at, id_], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(cursor: Optional[str]) -> Optional[Tuple[str, str]]:
    """(occurred_at, id) from a token, or None when absent. A malformed token
    is a 422 — never silently ignored, which would restart the walk at the head
    and double-count."""
    if not cursor:
        return None
    try:
        occurred_at, id_ = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return str(occurred_at), str(id_)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="invalid cursor") from exc


def _keyset_predicate(prefix: str = "") -> str:
    """DESC keyset over (occurred_at, id). A null cursor passes every row. The
    `id` comparison on a timestamp tie is REQUIRED — without it an exclusive
    occurred_at cursor eats the remainder of each boundary group (#323)."""
    return (
        f"($cursor_ts IS NULL "
        f"OR {prefix}occurred_at < $cursor_ts "
        f"OR ({prefix}occurred_at = $cursor_ts AND {prefix}id < $cursor_id))"
    )


def _cursor_params(cursor: Optional[str]) -> dict:
    decoded = _decode_cursor(cursor)
    ts, id_ = decoded if decoded else (None, None)
    return {"cursor_ts": ts, "cursor_id": id_}


def _paginate(rows: list, limit: int, key) -> Tuple[list, bool, Optional[str]]:
    """Trim an over-fetched (limit+1) result set to `limit`, report `has_more`,
    and build `next_cursor` from the last kept row via
    ``key(row) -> (occurred_at, id)``."""
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = _encode_cursor(*key(page[-1])) if has_more and page else None
    return page, has_more, next_cursor


def ensure_decision_vector_index(kg: MicrobiomeKG, dimension: int) -> None:
    """Create the :Decision(embedding) vector index (idempotent, #190 pillar 4).

    ``dimension`` is interpolated into the DDL — it's a config-derived int and
    Cypher index options accept no bound parameters.
    """
    kg.execute_cypher(
        f"""
        CREATE VECTOR INDEX decision_embedding IF NOT EXISTS
        FOR (d:Decision) ON (d.embedding)
        OPTIONS {{indexConfig: {{
            `vector.dimensions`: {int(dimension)},
            `vector.similarity_function`: 'cosine'
        }}}}
        """
    )


# --- The DecisionEvent contract -------------------------------------------

#: `sha256:` + 64 hex, matched case-insensitively then stored lowercased
#: (Workbench#688 emits lowercase; convention is lowercase).
_CHECKSUM_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class EvidenceRef(BaseModel):
    """One evidence reference with optional content identity (#321).

    Workbench#688 emits `{uri, checksum, role}`; older producers and the
    `git_provenance` adapter emit a bare URI string, normalized to `{uri}` by
    `DecisionEvent._accept_v4`. `checksum` pins *which* artifact (so #310 can
    build a `:Artifact` node on it); `role` (INPUT/OUTPUT) is what lets run N's
    output join run N+1's input.
    """

    uri: str
    checksum: Optional[str] = None
    role: Optional[str] = None

    @field_validator("checksum")
    @classmethod
    def _valid_checksum(cls, v: Optional[str]) -> Optional[str]:
        # Absence is meaningful ("not hashed") — a blank or malformed value is
        # ignored rather than stored, and never becomes an empty string (#321).
        if not v or not v.strip():
            return None
        v = v.strip().lower()
        return v if _CHECKSUM_RE.match(v) else None

    @field_validator("role")
    @classmethod
    def _norm_role(cls, v: Optional[str]) -> Optional[str]:
        v = (v or "").strip()
        return v or None


class DecisionEvent(BaseModel):
    """A single decision/action emitted by a MicroMap tool, with the entities
    it is about and the evidence that grounds it.

    Accepts both the old flat contract and the Nexus §4 nested contract:
    - If occurred_at is missing, value is taken from source_created_at
    - If actor is nested {user_id, org_id, role}, it is flattened
    - If entity_tags is a list of dicts, keys are extracted
    - If evidence_refs is a list of dicts, uri/url/ref fields are extracted
    - If tool is missing, it is extracted from source_ref.tool
    - supersedes is unioned with source_ref.parents (PROV-7)
    """

    id: str = Field(..., min_length=1, description="Stable unique id, e.g. 'dec-006'.")
    tool: str = Field(..., description="Emitting tool: nexus | workbench | mapforge.")
    action_type: str = Field(
        ...,
        description="target_rationale | committee_gate | pipeline_run | "
        "hypothesis_verdict | data_contribution.",
    )
    decision_outcome: str = Field(
        "",
        description="The verdict/outcome of the decision, e.g. supported | "
        "refuted | pass | fail | approved | merged. Empty when not applicable. "
        "Carried end-to-end so a hypothesis_verdict's result is not lost.",
    )
    occurred_at: str = Field(..., description="ISO-8601 timestamp, e.g. '2026-06-08T17:00:00Z'.")
    summary: str = Field(..., description="One-line what.")
    rationale: str = Field("", description="The why.")
    evidence_refs: List[str] = Field(
        default_factory=list,
        description="Citation URIs (flat). Derived from `evidence`; kept as bare "
        "strings for backward compatibility.",
    )
    evidence: List[EvidenceRef] = Field(
        default_factory=list,
        description="Structured evidence entries {uri, checksum?, role?} (#321). "
        "Carries Workbench#688's content identity (checksum) and consumed/produced "
        "role. Bare-string evidence_refs normalize to {uri} entries.",
    )
    actor_user: str = Field("", description="Who acted.")
    actor_org: str = Field("", description="Their org.")
    role: str = Field("", description="Their role, e.g. ceo | scientist | service.")
    entity_tags: List[str] = Field(
        default_factory=list,
        description="Canonical entity keys this decision is ABOUT: Disease "
        "name_normalized (e.g. 'parkinson disease') or Taxon taxon_id "
        "(e.g. 'NCBITaxon:853').",
    )
    supersedes: List[str] = Field(
        default_factory=list,
        description="IDs of prior decisions that this decision supersedes. "
        "Also populated from source_ref.parents (git adapter / PROV-6). "
        "For each listed id that exists as a :Decision, a "
        "(:Decision {id})-[:SUPERSEDED_BY]->(this) edge is created.",
    )
    recorded_by_analysis_ids: List[str] = Field(
        default_factory=list,
        description="IDs of spine :Analysis nodes that recorded this decision. "
        "For each that exists in this org, a (:Analysis)-[:RECORDED]->(:Decision) "
        "edge is created (#258). Unknown/foreign ids are no-ops.",
    )
    context_snapshot: Optional[Dict[str, Any]] = Field(
        default=None,
        description="State-at-decision-time snapshot (e.g. the verdict object, "
        "caveats) — the spec's `context_snapshot` / decision_log `context JSONB`. "
        "Stored on the :Decision node as a JSON string for now; its canonical "
        "home is the Postgres append-only event log (JSONB) once that lands (#190).",
    )
    source_native_id: str = Field(
        "",
        description="The producing system's own id for the source record "
        "(`source_ref.native_id`) — a back-link from the decision to the record "
        "it came from. Extracted from a nested `source_ref` if present.",
    )
    # NOTE: organization_id is NOT a request field — it's derived from the
    # caller's API key (resolve_organization_id), so a client cannot write to
    # another org by setting it in the body.

    @model_validator(mode="before")
    @classmethod
    def _accept_v4(cls, data):
        """Normalize Nexus §4 payload shape to the flat internal contract."""
        if not isinstance(data, dict):
            return data
        d = dict(data)
        # 1. occurred_at <- source_created_at fallback
        if not d.get("occurred_at") and d.get("source_created_at"):
            d["occurred_at"] = d["source_created_at"]
        # 2. flatten nested actor
        actor = d.get("actor")
        if isinstance(actor, dict):
            d.setdefault("actor_user", actor.get("user_id") or "")
            d.setdefault("actor_org", actor.get("org_id") or "")
            d.setdefault("role", actor.get("role") or "")
        # 3. entity_tags: list[dict|str] -> list[str] (extract "key")
        d["entity_tags"] = [
            (t.get("key") if isinstance(t, dict) else t)
            for t in (d.get("entity_tags") or [])
            if (t.get("key") if isinstance(t, dict) else t)
        ]
        # 4. evidence_refs: list[dict|str] -> structured `evidence` {uri, checksum?,
        #    role?}, preserving sibling keys instead of flattening them away (#321).
        #    `evidence_refs` stays the flat URI list for backward compatibility.
        def _uri(x):
            if isinstance(x, dict):
                return x.get("uri") or x.get("url") or x.get("ref")
            return x
        structured: List[dict] = []
        for x in (d.get("evidence_refs") or []):
            uri = _uri(x)
            if not uri:
                continue
            entry = {"uri": uri}
            if isinstance(x, dict):
                if x.get("checksum"):
                    entry["checksum"] = x["checksum"]
                if x.get("role"):
                    entry["role"] = x["role"]
            structured.append(entry)
        d["evidence"] = structured
        d["evidence_refs"] = [e["uri"] for e in structured]
        # 5. tool + native_id from source_ref
        if isinstance(d.get("source_ref"), dict):
            if not d.get("tool"):
                d["tool"] = d["source_ref"].get("tool", "")
            if not d.get("source_native_id") and d["source_ref"].get("native_id"):
                d["source_native_id"] = d["source_ref"]["native_id"]
        # 6. supersedes: union of explicit field and source_ref.parents (PROV-7)
        explicit: List[str] = list(d.get("supersedes") or [])
        parents: List[str] = []
        if isinstance(d.get("source_ref"), dict):
            parents = list(d["source_ref"].get("parents") or [])
        combined = list(dict.fromkeys(explicit + parents))  # dedup, preserve order
        d["supersedes"] = [p for p in combined if p]        # filter empty strings
        return d


class DecisionIngestResult(BaseModel):
    id: str
    organization_id: str
    entity_tags_matched: int
    entity_tags_unmatched: List[str]
    supersedes_linked: int = 0
    recorded_links_created: int = 0
    artifacts_written: int = 0
    derived_from_created: int = 0


@router.post(
    "/provenance/decisions",
    status_code=201,
    response_model=DecisionIngestResult,
)
async def ingest_decision(
    event: DecisionEvent,
    organization_id: str = Depends(resolve_organization_id),
) -> DecisionIngestResult:
    """Record a decision: MERGE the `:Decision` and link `:ABOUT` edges to the
    canonical entities named in `entity_tags`. Idempotent on `id`.

    `organization_id` is derived from the caller's API key — a client cannot
    write into another org. Unmatched tags are reported (not silently dropped)
    so a caller can tell when a decision points at an entity not yet in the graph.

    If `supersedes` is non-empty, SUPERSEDED_BY edges are created from each
    listed prior decision to this one (PROV-7). Unknown prior ids are no-ops.

    If `recorded_by_analysis_ids` is non-empty, a (:Analysis)-[:RECORDED]->
    (:Decision) edge is created from each existing, same-org spine analysis to
    this decision (#258). Unknown/foreign ids are no-ops.
    """
    # 0. Append to the Postgres append-only event log (the system of record) when
    #    configured. Durable: a failure here fails the request (503) so the event
    #    is never silently lost. The Neo4j graph below is a rebuildable projection.
    #    Skipped cleanly when DATABASE_URL is unset (Neo4j-only deploys).
    if decision_log.is_enabled():
        try:
            decision_log.append(event.model_dump(), organization_id)
        except psycopg.Error as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"decision event log unavailable: {type(exc).__name__}",
            ) from exc

    kg = get_kg()

    # 1. MERGE the decision node (org from the authenticated key). Idempotent on id.
    #    context_snapshot is a nested object; Neo4j has no map property type, so we
    #    store it as a JSON string (interim — its canonical home is the Postgres
    #    JSONB event log, #190). Excluded from model_dump and re-added serialized.
    params = event.model_dump(
        exclude={"entity_tags", "supersedes", "context_snapshot",
                 "recorded_by_analysis_ids", "evidence"})
    params["organization_id"] = organization_id
    params["context_snapshot"] = (
        json.dumps(event.context_snapshot, sort_keys=True)
        if event.context_snapshot
        else None
    )
    # Neo4j has no map/list-of-map property type, so structured evidence rides
    # as a JSON string on the node (like context_snapshot). Only stored when an
    # entry actually carries checksum/role, so bare-URI (legacy) decisions keep
    # a null d.evidence and are not bloated. The flat d.evidence_refs (URIs) is
    # unchanged, so the citation/query surface is unaffected (#321).
    params["evidence"] = (
        json.dumps([e.model_dump() for e in event.evidence], sort_keys=True)
        if any(e.checksum or e.role for e in event.evidence)
        else None
    )
    kg.execute_cypher(
        """
        MERGE (d:Decision {id: $id})
        SET d.tool = $tool,
            d.action_type = $action_type,
            d.decision_outcome = $decision_outcome,
            d.occurred_at = $occurred_at,
            d.summary = $summary,
            d.rationale = $rationale,
            d.evidence_refs = $evidence_refs,
            d.evidence = $evidence,
            d.actor_user = $actor_user,
            d.actor_org = $actor_org,
            d.role = $role,
            d.source_native_id = $source_native_id,
            d.context_snapshot = $context_snapshot,
            d.organization_id = $organization_id
        """,
        params,
    )

    # 2. Link :ABOUT edges to canonical entities; report which tags matched.
    unmatched: List[str] = []
    matched = 0
    if event.entity_tags:
        # Disease tags must be matched on `name_normalized` using the SAME
        # normalization the loaders apply (normalize_disease_name) — a bare
        # toLower misses apostrophe/abbreviation differences, so "Crohn's
        # Disease" / "IBD" never matched the graph's "crohns disease" /
        # "inflammatory bowel disease" keys (#201 §4). normalize_disease_name is
        # Python, not Cypher, so we normalize host-side and pass both forms:
        # Disease matches `norm`, Taxon matches `raw` (taxon_id is verbatim).
        # normalize_disease_name is idempotent, so already-normalized tags are
        # unaffected.
        tag_pairs = [
            {"raw": t, "norm": normalize_disease_name(t)} for t in event.entity_tags
        ]
        rows = kg.execute_cypher(
            """
            MATCH (d:Decision {id: $id})
            UNWIND $tag_pairs AS tp
            OPTIONAL MATCH (e)
              WHERE (e:Disease AND e.name_normalized = tp.norm)
                 OR (e:Taxon   AND e.taxon_id = tp.raw)
            FOREACH (_ IN CASE WHEN e IS NULL THEN [] ELSE [1] END |
                     MERGE (d)-[:ABOUT]->(e))
            RETURN tp.raw AS tag, e IS NOT NULL AS matched
            """,
            {"id": event.id, "tag_pairs": tag_pairs},
        )
        for row in rows:
            if row["matched"]:
                matched += 1
            else:
                unmatched.append(row["tag"])

    # 3. Create SUPERSEDED_BY edges: (prior)-[:SUPERSEDED_BY]->(this). (PROV-7)
    #    Unknown prior ids are no-ops (OPTIONAL MATCH + FOREACH guard).
    supersedes_linked = 0
    if event.supersedes:
        sup_rows = kg.execute_cypher(
            """
            MATCH (this:Decision {id: $id})
            UNWIND $supersedes AS prior_id
            OPTIONAL MATCH (p:Decision {id: prior_id})
            FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END |
                     MERGE (p)-[:SUPERSEDED_BY]->(this))
            RETURN prior_id, p IS NOT NULL AS linked
            """,
            {"id": event.id, "supersedes": event.supersedes},
        )
        for row in sup_rows:
            if row["linked"]:
                supersedes_linked += 1

    # 3b. #258: link the spine analyses that recorded this decision, org-scoped
    #     and non-gating. Unknown/foreign analysis ids are no-ops.
    recorded_links_created = 0
    if event.recorded_by_analysis_ids:
        rec_rows = kg.execute_cypher(
            """
            MATCH (d:Decision {id: $id, organization_id: $org})
            UNWIND $aids AS aid
            OPTIONAL MATCH (an:Analysis {id: aid, organization_id: $org})
            FOREACH (_ IN CASE WHEN an IS NULL THEN [] ELSE [1] END |
                     MERGE (an)-[:RECORDED]->(d))
            RETURN aid, an IS NOT NULL AS linked
            """,
            {"id": event.id, "org": organization_id,
             "aids": event.recorded_by_analysis_ids},
        )
        recorded_links_created = sum(1 for r in rec_rows if r["linked"])

    # 3c. #310: materialize :Artifact nodes + PRODUCED/USED/DERIVED_FROM from the
    #     structured evidence carrying a content hash (#321). An output artifact
    #     of this decision is DERIVED_FROM each of its input artifacts — the
    #     standard PROV "output was derived from the activity's inputs" — so a
    #     later run that USES this output traces back through it (cross-run
    #     lineage). Org-scoped; content-only evidence (no role) still records the
    #     :Artifact identity but no directional edge.
    artifacts_written = 0
    derived_from_created = 0
    checksummed = [e for e in event.evidence if e.checksum]
    if checksummed:
        art_rows = kg.execute_cypher(
            ARTIFACT_MERGE_CYPHER,
            {"id": event.id, "org": organization_id,
             "artifacts": [
                 {"checksum": e.checksum, "uri": e.uri, "role": (e.role or "").upper()}
                 for e in checksummed
             ]},
        )
        artifacts_written = art_rows[0]["artifacts"] if art_rows else 0

        df_rows = kg.execute_cypher(DERIVED_FROM_CYPHER, {"id": event.id})
        derived_from_created = df_rows[0]["derived"] if df_rows else 0

    # 4. Semantic embedding (#190 pillar 4): embed summary+rationale and store it
    #    on the node for vector search. BEST-EFFORT — embeddings are a rebuildable
    #    search aid, not the system of record, so an embedder outage never blocks
    #    recording the decision. Skipped when embeddings are unconfigured.
    embedder = embeddings.get_embedder()
    if embedder is not None:
        try:
            vec = embedder.embed_documents([decision_text(event.summary, event.rationale)])[0]
            kg.execute_cypher(
                "MATCH (d:Decision {id: $id}) SET d.embedding = $embedding",
                {"id": event.id, "embedding": vec},
            )
        except Exception:
            logger.exception("decision %s: embedding skipped (non-gating)", event.id)

    return DecisionIngestResult(
        id=event.id,
        organization_id=organization_id,
        entity_tags_matched=matched,
        entity_tags_unmatched=unmatched,
        supersedes_linked=supersedes_linked,
        artifacts_written=artifacts_written,
        derived_from_created=derived_from_created,
        recorded_links_created=recorded_links_created,
    )


# --- Query surface (the cited "decisions about target X" answer) ----------

class DecisionQuery(BaseModel):
    """Ask: what decisions were made about entity X, and why?"""

    entity: str = Field(
        ...,
        min_length=1,
        description="Case-insensitive substring of an entity name, e.g. "
        "'parkinson' or 'faecalibacterium'.",
    )
    # org scope is derived from the caller's API key, not the request body.
    limit: int = Field(50, ge=1, le=500)
    cursor: Optional[str] = Field(
        None,
        description="Opaque continuation token from a previous page's "
        "`next_cursor`. Walk the full history by following it until "
        "`has_more` is false (#323).",
    )


class AsOfQuery(BaseModel):
    """Ask: what decisions about entity X were current as of a given date?"""

    entity: str = Field(
        ...,
        min_length=1,
        description="Case-insensitive substring of an entity name.",
    )
    as_of: str = Field(
        ...,
        description="ISO-8601 date/timestamp upper bound (inclusive), e.g. '2026-06-08'.",
    )
    limit: int = Field(50, ge=1, le=500)
    cursor: Optional[str] = Field(None, description="Continuation token (#323).")


class CitedDecision(BaseModel):
    id: str
    action_type: str
    decision_outcome: str = ""
    summary: str
    rationale: str
    tool: str
    occurred_at: str
    actor_user: str
    actor_org: str
    role: str
    about: List[str]       # all canonical entities this decision is ABOUT
    citations: List[str]   # evidence_refs


class DecisionQueryResult(BaseModel):
    entity: str
    count: int  # rows in THIS page, not the total matching — see has_more (#323)
    decisions: List[CitedDecision]
    has_more: bool = False
    next_cursor: Optional[str] = None


@router.post("/provenance/query", response_model=DecisionQueryResult)
async def query_decisions(
    query: DecisionQuery,
    organization_id: str = Depends(resolve_organization_id),
) -> DecisionQueryResult:
    """Return decisions ABOUT any entity whose name contains `entity`, newest
    first, each grounded by its `evidence_refs` as citations. Scoped to the
    caller's org (from their API key).

    A decision is included if *any* of its ABOUT entities matches; the returned
    `about` list is the decision's full entity set (so the cited chain shows the
    whole context, e.g. Parkinson + the butyrate guild), not just the match.
    """
    kg = get_kg()
    params = query.model_dump()
    params["organization_id"] = organization_id
    params.update(_cursor_params(query.cursor))
    params["limit_plus1"] = query.limit + 1
    rows = kg.execute_cypher(
        f"""
        MATCH (d:Decision)-[:ABOUT]->(match)
        WHERE toLower(match.name) CONTAINS toLower($entity)
          AND ($organization_id IS NULL OR d.organization_id = $organization_id)
          AND {_keyset_predicate("d.")}
        WITH DISTINCT d
        MATCH (d)-[:ABOUT]->(e)
        WITH d, collect(DISTINCT e.name) AS about
        RETURN d.id AS id,
               d.action_type AS action_type,
               coalesce(d.decision_outcome, '') AS decision_outcome,
               d.summary AS summary,
               coalesce(d.rationale, '') AS rationale,
               d.tool AS tool,
               d.occurred_at AS occurred_at,
               coalesce(d.actor_user, '') AS actor_user,
               coalesce(d.actor_org, '') AS actor_org,
               coalesce(d.role, '') AS role,
               about,
               coalesce(d.evidence_refs, []) AS citations
        ORDER BY occurred_at DESC, id DESC
        LIMIT $limit_plus1
        """,
        params,
    )
    page, has_more, next_cursor = _paginate(
        rows, query.limit, lambda r: (r["occurred_at"], r["id"]))
    decisions = [CitedDecision(**row) for row in page]
    return DecisionQueryResult(
        entity=query.entity, count=len(decisions), decisions=decisions,
        has_more=has_more, next_cursor=next_cursor)


@router.post("/provenance/current", response_model=DecisionQueryResult)
async def query_current_decisions(
    query: DecisionQuery,
    organization_id: str = Depends(resolve_organization_id),
) -> DecisionQueryResult:
    """Return the *current* (non-superseded) decisions about `entity`, newest
    first. A decision is current iff it has no outgoing SUPERSEDED_BY edge —
    i.e. nothing newer has declared it obsolete.

    Use this endpoint (instead of /provenance/query) when you want the live
    frame rather than the full history. (PROV-7)
    """
    kg = get_kg()
    params = query.model_dump()
    params["organization_id"] = organization_id
    params.update(_cursor_params(query.cursor))
    params["limit_plus1"] = query.limit + 1
    rows = kg.execute_cypher(
        f"""
        MATCH (d:Decision)-[:ABOUT]->(match)
        WHERE toLower(match.name) CONTAINS toLower($entity)
          AND ($organization_id IS NULL OR d.organization_id = $organization_id)
          AND NOT (d)-[:SUPERSEDED_BY]->()
          AND {_keyset_predicate("d.")}
        WITH DISTINCT d
        MATCH (d)-[:ABOUT]->(x)
        WITH d, collect(DISTINCT x.name) AS about
        RETURN d.id AS id,
               d.action_type AS action_type,
               coalesce(d.decision_outcome, '') AS decision_outcome,
               d.summary AS summary,
               coalesce(d.rationale, '') AS rationale,
               d.tool AS tool,
               d.occurred_at AS occurred_at,
               coalesce(d.actor_user, '') AS actor_user,
               coalesce(d.actor_org, '') AS actor_org,
               coalesce(d.role, '') AS role,
               about,
               coalesce(d.evidence_refs, []) AS citations
        ORDER BY occurred_at DESC, id DESC
        LIMIT $limit_plus1
        """,
        params,
    )
    page, has_more, next_cursor = _paginate(
        rows, query.limit, lambda r: (r["occurred_at"], r["id"]))
    decisions = [CitedDecision(**row) for row in page]
    return DecisionQueryResult(
        entity=query.entity, count=len(decisions), decisions=decisions,
        has_more=has_more, next_cursor=next_cursor)


@router.post("/provenance/as-of", response_model=DecisionQueryResult)
async def query_decisions_as_of(
    query: AsOfQuery,
    organization_id: str = Depends(resolve_organization_id),
) -> DecisionQueryResult:
    """Return decisions about `entity` that were *current as of* `as_of`.

    A decision is current-as-of D if:
      - its occurred_at <= D, AND
      - it is not superseded by any decision whose occurred_at <= D.

    This answers "what did we believe on date D?" for the given entity. (PROV-7)
    """
    kg = get_kg()
    params = {
        "entity": query.entity,
        "as_of": _as_of_upper_bound(query.as_of),
        "organization_id": organization_id,
        "limit_plus1": query.limit + 1,
        **_cursor_params(query.cursor),
    }
    rows = kg.execute_cypher(
        f"""
        MATCH (d:Decision)-[:ABOUT]->(match)
        WHERE toLower(match.name) CONTAINS toLower($entity)
          AND d.occurred_at <= $as_of
          AND ($organization_id IS NULL OR d.organization_id = $organization_id)
          AND {_keyset_predicate("d.")}
          AND NOT EXISTS {{
            MATCH (d)-[:SUPERSEDED_BY]->(n:Decision)
            WHERE n.occurred_at <= $as_of
          }}
        WITH DISTINCT d
        MATCH (d)-[:ABOUT]->(x)
        WITH d, collect(DISTINCT x.name) AS about
        RETURN d.id AS id,
               d.action_type AS action_type,
               coalesce(d.decision_outcome, '') AS decision_outcome,
               d.summary AS summary,
               coalesce(d.rationale, '') AS rationale,
               d.tool AS tool,
               d.occurred_at AS occurred_at,
               coalesce(d.actor_user, '') AS actor_user,
               coalesce(d.actor_org, '') AS actor_org,
               coalesce(d.role, '') AS role,
               about,
               coalesce(d.evidence_refs, []) AS citations
        ORDER BY occurred_at DESC, id DESC
        LIMIT $limit_plus1
        """,
        params,
    )
    page, has_more, next_cursor = _paginate(
        rows, query.limit, lambda r: (r["occurred_at"], r["id"]))
    decisions = [CitedDecision(**row) for row in page]
    return DecisionQueryResult(
        entity=query.entity, count=len(decisions), decisions=decisions,
        has_more=has_more, next_cursor=next_cursor)


# --- Unified ledger: :Decision ∪ :Contribution (responsibility 6) ---------

class LedgerQuery(BaseModel):
    """The provenance ledger: decisions about entity X, unioned with the
    MapForge data contributions that fed the graph."""

    entity: str = Field(..., min_length=1, description="Substring of an entity name to filter decisions.")
    # org scope is derived from the caller's API key, not the request body.
    limit: int = Field(100, ge=1, le=1000)
    cursor: Optional[str] = Field(None, description="Continuation token (#323).")


class LedgerEntry(BaseModel):
    source: str   # tool that emitted it (nexus|workbench|mapforge)
    kind: str     # action_type, or 'data_contribution' for :Contribution rows
    decision_outcome: str = ""  # verdict for :Decision rows; '' for :Contribution
    summary: str
    occurred_at: str


class LedgerResult(BaseModel):
    entity: str
    count: int  # rows in THIS page, not the total matching — see has_more (#323)
    entries: List[LedgerEntry]
    has_more: bool = False
    next_cursor: Optional[str] = None


@router.post("/provenance/ledger", response_model=LedgerResult)
async def provenance_ledger(
    query: LedgerQuery,
    organization_id: str = Depends(resolve_organization_id),
) -> LedgerResult:
    """Unified provenance ledger. The decision side is filtered to those ABOUT
    `entity`; the MapForge `:Contribution` side (written by `mapforge submit`)
    is surfaced natively — it's submission-level provenance with no per-entity
    ABOUT edges, so it's org-scoped but not entity-filtered, exactly as the
    #190 kickoff specifies. Newest first.

    The UNION is wrapped in a CALL{} subquery so the outer ORDER BY/LIMIT apply
    to the *combined* result (a bare `UNION ... ORDER BY` would bind only to the
    last branch in Cypher).
    """
    kg = get_kg()
    params = query.model_dump()
    params["organization_id"] = organization_id
    params.update(_cursor_params(query.cursor))
    params["limit_plus1"] = query.limit + 1
    # Both branches emit an `id` so the composite keyset applies across the union:
    # decisions key on d.id; :Contribution has no single id (it MERGEs on
    # mapping_sha256+source_sha256+org), so synthesize a stable one from its key.
    rows = kg.execute_cypher(
        f"""
        CALL {{
            MATCH (d:Decision)-[:ABOUT]->(e)
            WHERE toLower(e.name) CONTAINS toLower($entity)
              AND ($organization_id IS NULL OR d.organization_id = $organization_id)
            WITH DISTINCT d
            RETURN d.tool AS source, d.action_type AS kind,
                   coalesce(d.decision_outcome, '') AS decision_outcome,
                   d.summary AS summary, d.occurred_at AS occurred_at, d.id AS id
            UNION
            MATCH (c:Contribution)
            WHERE $organization_id IS NULL OR c.organization_id = $organization_id
            RETURN 'mapforge' AS source, 'data_contribution' AS kind,
                   '' AS decision_outcome,
                   c.source_name AS summary, c.submitted_at AS occurred_at,
                   c.mapping_sha256 + '|' + c.source_sha256 AS id
        }}
        WITH source, kind, decision_outcome, summary, occurred_at, id
        WHERE {_keyset_predicate("")}
        RETURN source, kind, decision_outcome, summary, occurred_at, id
        ORDER BY occurred_at DESC, id DESC
        LIMIT $limit_plus1
        """,
        params,
    )
    page, has_more, next_cursor = _paginate(
        rows, query.limit, lambda r: (r["occurred_at"], r["id"]))
    entries = [LedgerEntry(**row) for row in page]
    return LedgerResult(
        entity=query.entity, count=len(entries), entries=entries,
        has_more=has_more, next_cursor=next_cursor)


# --- Semantic search (#190 pillar 4) --------------------------------------

class DecisionSearchQuery(BaseModel):
    """Ask in natural language: find decisions whose summary+rationale are
    semantically nearest to `query` (vector search over :Decision embeddings)."""

    query: str = Field(
        ..., min_length=1,
        description="Free-text query, e.g. 'why are we pursuing the butyrate "
        "pathway for parkinson'.",
    )
    limit: int = Field(10, ge=1, le=100)


class ScoredDecision(CitedDecision):
    score: float


class DecisionSearchResult(BaseModel):
    query: str
    count: int  # rows in THIS page — see has_more (#323)
    decisions: List[ScoredDecision]
    has_more: bool = False
    # No next_cursor: results are relevance-ranked (score DESC), not a stable
    # time order, so this is top-k retrieval — not a walkable enumeration.


@router.post("/provenance/search", response_model=DecisionSearchResult)
async def search_decisions(
    query: DecisionSearchQuery,
    organization_id: str = Depends(resolve_organization_id),
) -> DecisionSearchResult:
    """Semantic search over decision summary+rationale via the Neo4j vector index,
    org-scoped, each grounded by its `evidence_refs`. Newest-similar first.

    Returns 503 when embeddings are not configured (set `EMBEDDINGS_PROVIDER`).
    Only decisions that carry an embedding (ingested while embeddings were
    enabled) are in the index — backfilling older decisions is a follow-up.
    """
    embedder = embeddings.get_embedder()
    if embedder is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="semantic search is not configured (set EMBEDDINGS_PROVIDER)",
        )
    kg = get_kg()
    vec = embedder.embed_query(query.query)
    # Over-fetch from the index (it can't filter by org), then org-scope + limit.
    params = {
        "k": min(query.limit * 5, 500),
        "vec": vec,
        "organization_id": organization_id,
        "limit_plus1": query.limit + 1,
    }
    rows = kg.execute_cypher(
        """
        CALL db.index.vector.queryNodes('decision_embedding', $k, $vec)
            YIELD node AS d, score
        WHERE ($organization_id IS NULL OR d.organization_id = $organization_id)
        OPTIONAL MATCH (d)-[:ABOUT]->(e)
        WITH d, score, collect(DISTINCT e.name) AS about
        RETURN d.id AS id,
               d.action_type AS action_type,
               coalesce(d.decision_outcome, '') AS decision_outcome,
               d.summary AS summary,
               coalesce(d.rationale, '') AS rationale,
               d.tool AS tool,
               d.occurred_at AS occurred_at,
               coalesce(d.actor_user, '') AS actor_user,
               coalesce(d.actor_org, '') AS actor_org,
               coalesce(d.role, '') AS role,
               about,
               coalesce(d.evidence_refs, []) AS citations,
               score
        ORDER BY score DESC
        LIMIT $limit_plus1
        """,
        params,
    )
    # Over-fetched by one to detect truncation; no keyset walk (relevance order).
    has_more = len(rows) > query.limit
    decisions = [ScoredDecision(**row) for row in rows[:query.limit]]
    return DecisionSearchResult(
        query=query.query, count=len(decisions), decisions=decisions,
        has_more=has_more)
