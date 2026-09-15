"""MCP tools for the GPV-254 provenance spine: write findings, read lineage.

`register_provenance_tools` mirrors the sibling `register_cypher_tools` /
`register_mapforge_tools` idiom: connection params are threaded in (not a
shared driver), and each tool builds its own driver for the call.

`write_finding` (from `micromap_mapforge.provenance.spine`) is synchronous —
same as the rest of that module — so the async tool wrapper calls it directly,
matching how `mapforge_submit` calls the sync `runner.submit(...)`.

`provenance_lineage` is a read tool tracing experiment -> analysis -> assertion
paths for an entity (current truth or as-of a timestamp). Its DEFAULT read is
scoped to the caller's own org plus the canonical orgs (`spine.canonical_orgs`),
via the shared `scoping.ORG_FILTER` (#284, #299) — a tenant sees its own
contributions and the shared reference data, but not another tenant's, and
non-canonical (demo/eval/rehearsal) assertions never surface as if they were
reference truth. Pass `include_noncanonical=True` (privileged callers only) to
drop the org filter entirely and read every org's contributions (demo/admin).

The REST route `api/routes/provenance_lineage.py` is scoped differently, not
"the same way": it resolves a single org via `resolve_organization_id` and
matches `a.organization_id = $organization_id` exactly, with no canonical/
public-org widening. A REST caller therefore does not see the shared
reference (canonical) data unless it happens to live in their own org — only
this MCP read does the own-org-OR-canonical union.
"""
from __future__ import annotations

from datetime import datetime, timezone

from neo4j import GraphDatabase

from micromap_mapforge.provenance.spine import (
    EndpointRef,
    ExperimentRecord,
    AnalysisRecord,
    AssertionRecord,
    FindingRecord,
    canonical_orgs,
    write_finding,
)

from ..auth import PRIVILEGED_ROLES, current_principal, default_org, principal_org
from ..scoping import ORG_FILTER, OrgScope, scope_params


def _dt(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v) if v else None


def _finding_from_payload(p: dict) -> FindingRecord:
    """Pure mapping from the MCP tool's JSON payload to spine records.

    Org id is inherited from the top-level `organization_id` onto every
    record. JSON key "object" maps to the dataclass field `object_` (a
    reserved-name dodge). `asserted_at`/`valid_from` default to the
    analysis's `occurred_at` when absent from an assertion row.
    """
    org = p["organization_id"]
    e = p["experiment"]
    a = p["analysis"]
    exp = ExperimentRecord(
        id=e["id"], organization_id=org, title=e.get("title", ""),
        nexus_ref=e.get("nexus_ref", ""), started_at=_dt(e.get("started_at")),
        actor_user=e.get("actor_user", ""), actor_org=e.get("actor_org", ""),
    )
    an = AnalysisRecord(
        id=a["id"], organization_id=org, method=a.get("method", "agent-reasoning"),
        occurred_at=_dt(a["occurred_at"]) or datetime.now(timezone.utc),
        summary=a.get("summary", ""), model=a.get("model", ""),
        confidence=a.get("confidence"),
    )
    assertions = []
    for row in p["assertions"]:
        assertions.append(AssertionRecord(
            predicate=row["predicate"],
            subject=EndpointRef(**row["subject"]),
            object_=EndpointRef(**row["object"]),
            confidence=float(row["confidence"]),
            organization_id=org,
            asserted_at=_dt(row.get("asserted_at")) or an.occurred_at,
            valid_from=_dt(row.get("valid_from")) or an.occurred_at,
            valid_to=_dt(row.get("valid_to")),
            analysis_id=an.id,
            direction=row.get("direction", ""),
            evidence_refs=list(row.get("evidence_refs", [])),
            supersedes=list(row.get("supersedes", [])),
        ))
    return FindingRecord(experiment=exp, analysis=an, assertions=assertions)


# Lineage query fragments — keep in sync with api/routes/provenance_lineage.py.
# That REST module is the semantic source of truth for the lineage query; it is
# org-scoped to the caller by API key (an exact match, see the module docstring
# above). As of #299, this MCP copy adds broader scoping on top — the caller's
# own org OR the canonical/public orgs, via the shared ORG_FILTER built in
# _lineage_cypher below (not a fixed fragment here, since the filtered
# variable's org values must be bound params, never inlined).
_LIN_BASE = (
    "MATCH (a:Assertion)-[:SUBJECT|OBJECT]->(e) "
    "WHERE (toLower(e.name) = toLower($entity) OR e.name_normalized = toLower($entity)) "
)
_LIN_CURRENT = "AND NOT (a)-[:SUPERSEDED_BY]->() "
_LIN_ASOF = (
    "AND a.valid_from <= $as_of AND (a.valid_to IS NULL OR a.valid_to > $as_of) "
    "AND NOT EXISTS { MATCH (a)-[:SUPERSEDED_BY]->(n:Assertion) WHERE n.valid_from <= $as_of } "
)
_LIN_TAIL = (
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


def _lineage_cypher(as_of: str | None, entity: str = "", limit: int = 50,
                    include_noncanonical: bool = False,
                    scope: "OrgScope | None" = None):
    """Build the (cypher, params) pair for the lineage read — current or as-of.

    Visibility is the caller's own org OR the canonical/reference orgs (#284,
    #299), expressed with the shared ORG_FILTER so the org values are always
    bound parameters and never appear in the query text. Before #299 this was
    canonical-only, which meant a tenant could not see its own contributions.
    `include_noncanonical=True` drops the filter entirely and is role-gated by
    the tool.
    """
    params: dict = {"entity": entity, "limit": limit}
    canon = ""
    if not include_noncanonical:
        visible = scope or OrgScope(org_id=default_org(),
                                    public_orgs=list(canonical_orgs()))
        canon = "AND " + ORG_FILTER("a") + " "
        params.update(scope_params(visible))
    if as_of:
        params["as_of"] = as_of
        return _LIN_BASE + canon + _LIN_ASOF + "WITH DISTINCT a " + _LIN_TAIL, params
    return _LIN_BASE + canon + _LIN_CURRENT + "WITH DISTINCT a " + _LIN_TAIL, params


def register_provenance_tools(
    *,
    app,
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    database: str,
    return_callables: bool = False,
) -> dict:
    """Register the provenance-spine MCP tool(s) on `app`.

    Unlike `register_cypher_tools` (async driver), the spine's `write_finding`
    is synchronous, so each call builds and closes its own sync `GraphDatabase`
    driver — mirroring how `mapforge_submit` wraps the sync `MapForgeRunner`.
    """

    async def provenance_record_finding(experiment: dict, analysis: dict,
                                        assertions: list[dict],
                                        decision_ids: list[str] | None = None) -> dict:
        """Record an Experiment -> Analysis -> Assertion finding and project the edge(s).

        Args:
            experiment: {id, title?, nexus_ref?, started_at?, actor_user?, actor_org?}
            analysis: {id, method?, occurred_at, summary?, model?, confidence?}
            assertions: list of {subject, predicate, object, confidence, direction?,
                asserted_at?, valid_from?, valid_to?, evidence_refs?, supersedes?}
                where subject/object are {label, key_field, value}.
            decision_ids: optional ids of :Decision nodes this analysis recorded;
                each is linked (:Analysis)-[:RECORDED]->(:Decision), org-scoped,
                non-gating (unknown ids are ignored).

        The org is taken from the authenticated principal, never from the
        caller (#299) — accepting it as an argument let any caller write data
        stamped as another tenant. It is inherited onto every record.

        Writes the Experiment/Analysis/Assertion nodes and HAS_ANALYSIS/ASSERTED
        edges, then — for wired predicates whose subject/object labels resolve —
        projects the concrete KG edge. Returns experiment_id, analysis_id,
        resolved_count, unresolved_count, and per-assertion outcomes.
        """
        organization_id = principal_org()
        rec = _finding_from_payload({
            "experiment": experiment, "analysis": analysis,
            "assertions": assertions, "organization_id": organization_id,
        })
        driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
        try:
            return write_finding(driver, rec, database=database,
                                 link_decision_ids=decision_ids)
        finally:
            driver.close()

    async def provenance_lineage(entity: str, as_of: str | None = None, limit: int = 50,
                                 include_noncanonical: bool = False) -> dict:
        """Trace experiment -> analysis -> assertion lineage for an entity (current or as-of).

        Superseded assertions are excluded from current reads. Results are scoped
        to the caller's own org plus the canonical/reference orgs by default
        (#284, #299) — a tenant sees its own contributions and the shared
        reference data, never another tenant's. Set include_noncanonical=True to
        drop the filter entirely (privileged callers only).

        include_noncanonical is role-gated (#299): only PRIVILEGED_ROLES callers
        may drop the org filter — everyone else is silently held to the default,
        so a caller-flipped boolean can no longer surface another org's data.
        """
        p = current_principal()
        role = (p.role if p else "").strip().lower()
        if role not in PRIVILEGED_ROLES:
            include_noncanonical = False
        visible = OrgScope(org_id=principal_org(), public_orgs=list(canonical_orgs()))
        limit = max(1, min(limit, 500))
        cypher, params = _lineage_cypher(as_of, entity=entity, limit=limit,
                                         include_noncanonical=include_noncanonical,
                                         scope=visible)
        driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
        try:
            with driver.session(database=database) as session:
                rows = [r.data() for r in session.run(cypher, params)]
        finally:
            driver.close()
        return {"entity": entity, "as_of": as_of, "paths": rows, "count": len(rows)}

    callables = {
        "provenance_record_finding": provenance_record_finding,
        "provenance_lineage": provenance_lineage,
    }

    if app is not None:
        for name, fn in callables.items():
            app.tool(name=name)(fn)

    if return_callables:
        return callables
    return {}
