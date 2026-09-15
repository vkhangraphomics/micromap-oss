"""GPV-254 provenance spine: Experiment -> Analysis -> Assertion, plus edge projection.

Mirrors the parameterized-writer pattern in decision.py / contribution.py: a dataclass,
a cypher_for_* builder returning (stmt, params) tuples, and a write_* executor. Neo4j can't
target a relationship from a node, so :Assertion reifies each KG edge and carries confidence
+ valid-time; the concrete edge is a projection of the current assertion.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def canonical_orgs() -> frozenset[str]:
    """Orgs whose assertions represent shared/reference truth and may therefore
    project a concrete edge into the graph that *every* caller reads (#284).

    Resolved from env: ``CANONICAL_ORGS`` (comma-separated) overrides, else
    ``PUBLIC_ORGS`` (so one env can govern both what is publicly readable and
    what may project — mirrors ``api/scoping.get_public_orgs``), else the single
    loader default ``default`` (``load_knowledge_graph.DEFAULT_ORG_ID``).

    Non-canonical (customer / eval / rehearsal) assertions still record full
    lineage, but must not mutate the shared reference edges — that leak is what
    surfaced ``demo``/``intrinsic-eval`` rehearsal records above curated
    evidence in unscoped reads.
    """
    raw = os.environ.get("CANONICAL_ORGS") or os.environ.get("PUBLIC_ORGS") or "default"
    orgs = frozenset(o.strip() for o in raw.split(",") if o.strip())
    return orgs or frozenset({"default"})


def is_canonical_org(org: str, canonical: frozenset[str] | None = None) -> bool:
    """Whether ``org`` is allowed to project into the shared reference graph.

    ``canonical`` defaults to :func:`canonical_orgs` (env-resolved); pass it
    explicitly to decide against a fixed set without touching the environment.
    """
    return org in (canonical if canonical is not None else canonical_orgs())


# Relationship types the projection (Task 4) will materialize. The ONLY place a
# rel-type literal is built into Cypher — from THIS constant, never user input.
WIRED_PREDICATES: dict[str, tuple[str, str]] = {
    "ASSOCIATED_WITH_DISEASE": ("Taxon", "Disease"),
    "PRODUCES": ("Taxon", "Compound"),
}

# (label, key_field) pairs backed by a real index/constraint. For these, endpoint
# resolution emits a literal `(:Label {key: $value})` so the planner uses a
# NodeIndexSeek instead of scanning every node (#259). Membership here is the
# ONLY gate that promotes a label/key to a Cypher literal — exactly like
# WIRED_PREDICATES gates the projection rel-type — so the literals are always
# trusted constants, never caller-supplied strings. Any pair NOT listed falls
# back to the dynamic labels()/n[$key] scan, preserving resolve-if-it-exists for
# endpoints without a matching index. Keep in sync with the indexes declared in
# database/neo4j_schema.py and database/load_knowledge_graph.py.
INDEXED_ENDPOINTS: frozenset[tuple[str, str]] = frozenset({
    ("Taxon", "ncbi_tax_id"),       # neo4j_schema.py: taxon_ncbi_tax_id
    ("Taxon", "taxon_id"),          # neo4j_schema.py: taxon_id_unique constraint
    ("Disease", "name_normalized"),  # neo4j_schema.py: disease_normalized_name_idx
    ("Compound", "compound_id"),     # load_knowledge_graph.py: compound_id
    ("Compound", "hmdb_id"),         # load_knowledge_graph.py: compound_hmdb
    ("Compound", "kegg_id"),         # load_knowledge_graph.py: compound_kegg
    ("Compound", "name"),            # load_knowledge_graph.py: compound_name
})


@dataclass(frozen=True)
class EndpointRef:
    label: str        # e.g. "Taxon"
    key_field: str    # e.g. "ncbi_tax_id" | "taxon_id" | "name_normalized"
    value: str


@dataclass
class ExperimentRecord:
    id: str
    organization_id: str
    title: str = ""
    nexus_ref: str = ""
    started_at: datetime | None = None
    actor_user: str = ""
    actor_org: str = ""


@dataclass
class AnalysisRecord:
    id: str
    organization_id: str
    method: str
    occurred_at: datetime
    summary: str = ""
    model: str = ""
    confidence: float | None = None


@dataclass
class AssertionRecord:
    predicate: str
    subject: EndpointRef
    object_: EndpointRef
    confidence: float
    organization_id: str
    asserted_at: datetime
    valid_from: datetime
    analysis_id: str
    direction: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    valid_to: datetime | None = None
    supersedes: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        raw = f"{self.analysis_id}|{self.subject.value}|{self.predicate}|{self.object_.value}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class FindingRecord:
    experiment: ExperimentRecord
    analysis: AnalysisRecord
    assertions: list[AssertionRecord]


def _endpoint_clause(match_kw: str, var: str, ref: "EndpointRef", ref_prefix: str
                     ) -> tuple[str, dict[str, Any]]:
    """Build a node-match clause + params for one assertion endpoint.

    For an allowlisted (label, key_field) pair, emit a literal
    ``<match_kw> (var:Label {key: $<prefix>_value})`` so the planner hits the
    label+property index (NodeIndexSeek). The label and key literals come only
    from a pair already proven to be in ``INDEXED_ENDPOINTS`` — never from raw
    caller input — so this stays injection-safe, exactly like the
    ``WIRED_PREDICATES`` rel-type in :func:`cypher_for_projection`.

    Any other pair falls back to the dynamic ``labels()``/``var[$key]`` scan
    (AllNodesScan), preserving resolve-if-it-exists for endpoints whose
    (label, key) has no matching index. In every case the endpoint VALUE is a
    parameter, so it is never embedded in the query text.
    """
    if (ref.label, ref.key_field) in INDEXED_ENDPOINTS:
        clause = f"{match_kw} ({var}:{ref.label} {{{ref.key_field}: ${ref_prefix}_value}})"
        return clause, {f"{ref_prefix}_value": ref.value}
    clause = (f"{match_kw} ({var}) WHERE ${ref_prefix}_label IN labels({var}) "
              f"AND {var}[${ref_prefix}_key] = ${ref_prefix}_value")
    return clause, {f"{ref_prefix}_label": ref.label, f"{ref_prefix}_key": ref.key_field,
                    f"{ref_prefix}_value": ref.value}


def cypher_for_finding(rec: "FindingRecord") -> list[tuple[str, dict[str, Any]]]:
    stmts: list[tuple[str, dict[str, Any]]] = []
    exp, an = rec.experiment, rec.analysis

    stmts.append((
        "MERGE (e:Experiment {id: $id}) "
        "SET e.organization_id = $organization_id, e.title = $title, "
        "    e.nexus_ref = $nexus_ref, e.started_at = $started_at, "
        "    e.actor_user = $actor_user, e.actor_org = $actor_org",
        {"id": exp.id, "organization_id": exp.organization_id, "title": exp.title,
         "nexus_ref": exp.nexus_ref,
         "started_at": exp.started_at.isoformat() if exp.started_at else None,
         "actor_user": exp.actor_user, "actor_org": exp.actor_org},
    ))
    stmts.append((
        "MERGE (an:Analysis {id: $id}) "
        "SET an.organization_id = $organization_id, an.method = $method, "
        "    an.occurred_at = $occurred_at, an.summary = $summary, "
        "    an.model = $model, an.confidence = $confidence",
        {"id": an.id, "organization_id": an.organization_id, "method": an.method,
         "occurred_at": an.occurred_at.isoformat(), "summary": an.summary,
         "model": an.model, "confidence": an.confidence},
    ))
    stmts.append((
        "MATCH (e:Experiment {id: $eid}), (an:Analysis {id: $anid}) "
        "MERGE (e)-[:HAS_ANALYSIS]->(an)",
        {"eid": exp.id, "anid": an.id},
    ))

    for a in rec.assertions:
        stmts.append((
            "MATCH (an:Analysis {id: $analysis_id}) "
            "MERGE (a:Assertion {id: $id}) "
            "SET a.predicate = $predicate, a.confidence = $confidence, "
            "    a.direction = $direction, a.evidence_refs = $evidence_refs, "
            "    a.asserted_at = $asserted_at, a.valid_from = $valid_from, "
            "    a.valid_to = $valid_to, a.organization_id = $organization_id "
            "MERGE (an)-[:ASSERTED]->(a)",
            {"analysis_id": a.analysis_id, "id": a.id, "predicate": a.predicate,
             "confidence": a.confidence, "direction": a.direction,
             "evidence_refs": a.evidence_refs, "asserted_at": a.asserted_at.isoformat(),
             "valid_from": a.valid_from.isoformat(),
             "valid_to": a.valid_to.isoformat() if a.valid_to else None,
             "organization_id": a.organization_id},
        ))
        # Non-gating SUBJECT/OBJECT: OPTIONAL MATCH, MERGE only if resolved.
        for role, ref in (("SUBJECT", a.subject), ("OBJECT", a.object_)):
            match_clause, ep_params = _endpoint_clause("OPTIONAL MATCH", "n", ref, "ep")
            stmts.append((
                "MATCH (a:Assertion {id: $id}) "
                f"{match_clause} "
                "FOREACH (_ IN CASE WHEN n IS NULL THEN [] ELSE [1] END | "
                f"         MERGE (a)-[:{role}]->(n))",
                {"id": a.id, **ep_params},
            ))
        if a.supersedes:
            stmts.append((
                "MATCH (this:Assertion {id: $id}) "
                "UNWIND $supersedes AS prior "
                "OPTIONAL MATCH (p:Assertion {id: prior}) "
                "FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END | "
                "         MERGE (p)-[:SUPERSEDED_BY]->(this))",
                {"id": a.id, "supersedes": a.supersedes},
            ))
    return stmts


def cypher_for_projection(predicate: str, subj: "EndpointRef", obj: "EndpointRef",
                          organization_id: str,
                          canonical: "frozenset[str] | None" = None
                          ) -> tuple[str, dict[str, Any]]:
    """Project a CANONICAL assertion onto the shared reference edge (#284).

    #297 Part B: the edge to update is selected by ORG, not by pattern. Only a
    NULL-org (ingested GMRepo/Disbiome) or canonical-org edge is the shared
    reference edge; a customer's parallel org-stamped edge (from
    :func:`cypher_for_parallel_projection`) must NEVER be matched here — a plain
    ``MERGE (s)-[r:TYPE]->(o)`` would pattern-match it and overwrite its org,
    leaking the customer's contribution to every caller. The `existing`
    OPTIONAL MATCH is org-scoped so this can't happen.
    """
    if predicate not in WIRED_PREDICATES:
        raise ValueError(f"predicate {predicate!r} is not wired for projection")
    # rel_type is an allowlisted literal (validated above) — safe to embed.
    rel_type = predicate
    canon = list(canonical if canonical is not None else canonical_orgs())
    s_clause, s_params = _endpoint_clause("MATCH", "s", subj, "s")
    o_clause, o_params = _endpoint_clause("MATCH", "o", obj, "o")
    stmt = (
        s_clause + " " + o_clause + " "
        "MATCH (s)<-[:SUBJECT]-(a:Assertion)-[:OBJECT]->(o) "
        "WHERE a.predicate = $predicate AND a.organization_id = $organization_id "
        "  AND NOT (a)-[:SUPERSEDED_BY]->() "
        "WITH s, o, a ORDER BY a.asserted_at DESC LIMIT 1 "
        # Select ONLY the shared reference edge (ingested null-org or canonical),
        # never a customer's parallel org-stamped edge.
        "OPTIONAL MATCH (s)-[existing:" + rel_type + "]->(o) "
        "  WHERE existing.organization_id IS NULL "
        "     OR existing.organization_id IN $canonical_orgs "
        "WITH s, o, a, existing "
        # No shared edge yet -> create one, fully stamped as spine-owned.
        "FOREACH (_ IN CASE WHEN existing IS NULL THEN [1] ELSE [] END | "
        f"  CREATE (s)-[r:{rel_type}]->(o) "
        "  SET r.confidence = a.confidence, r.asserted_at = a.asserted_at, "
        "      r.assertion_id = a.id, r.direction = a.direction, "
        "      r.organization_id = $organization_id, r.source = 'provenance-spine') "
        # A shared edge exists -> link the assertion. A spine-owned (or unset)
        # edge accepts the new confidence/direction/org (so supersede re-projection
        # still updates it), but an edge loaded from an ingestion source
        # (GMRepo/Disbiome/...) retains its source + confidence + direction + org
        # and only gains the assertion_id linkage — never overwrite ingested
        # provenance, and never relabel an ingested edge's org.
        "FOREACH (ex IN CASE WHEN existing IS NULL THEN [] ELSE [existing] END | "
        "  SET ex.assertion_id = a.id, ex.asserted_at = a.asserted_at, "
        "      ex.confidence = CASE WHEN ex.source IS NULL OR ex.source = 'provenance-spine' "
        "                          THEN a.confidence ELSE ex.confidence END, "
        "      ex.direction = CASE WHEN ex.source IS NULL OR ex.source = 'provenance-spine' "
        "                         THEN a.direction ELSE ex.direction END, "
        "      ex.organization_id = CASE WHEN ex.source IS NULL OR ex.source = 'provenance-spine' "
        "                               THEN $organization_id ELSE ex.organization_id END, "
        "      ex.source = COALESCE(ex.source, 'provenance-spine'))"
    )
    params = {
        **s_params, **o_params,
        "predicate": predicate, "organization_id": organization_id,
        "canonical_orgs": canon,
    }
    return stmt, params


def cypher_for_parallel_projection(predicate: str, subj: "EndpointRef",
                                   obj: "EndpointRef", organization_id: str
                                   ) -> tuple[str, dict[str, Any]]:
    """Project a NON-canonical (customer) assertion as a PARALLEL edge (#297 B).

    The MERGE is keyed on ``organization_id``, so the customer's edge is a
    distinct relationship next to the canonical (default) and ingested (null-org)
    edges — it never matches or mutates the shared reference. A customer edge is
    always spine-owned (a customer never owns an ingested edge), so unlike
    :func:`cypher_for_projection` there is no ingested-source guard on ON MATCH.
    Edge-org-aware reads (#297 Part A) scope this edge to the owning org, so it
    surfaces only to that customer (+ canonical), never leaking cross-org.

    INVARIANT for downstream consumers (#345): because the MERGE key includes
    ``organization_id``, one (subject, object) pair can be joined by SEVERAL of
    these edges — one per org. Per-caller reads are safe (they scope the edge to
    the caller's org). But any consumer that aggregates these edge types
    ORG-AGNOSTICALLY — a cross-org rollup, a stats/analytics pass — must count
    the ENDPOINT with ``count(DISTINCT <endpoint>)``, never ``count(r)`` /
    ``count(<endpoint>)``, or it double-counts the endpoint once per parallel
    edge. (A deliberate per-EDGE row total, e.g. "how many associations", is
    correctly a raw ``count`` — the rule is about per-ENTITY aggregates.) None
    exist today over these types; this note is the guard for when one is added.
    """
    if predicate not in WIRED_PREDICATES:
        raise ValueError(f"predicate {predicate!r} is not wired for projection")
    rel_type = predicate
    s_clause, s_params = _endpoint_clause("MATCH", "s", subj, "s")
    o_clause, o_params = _endpoint_clause("MATCH", "o", obj, "o")
    stmt = (
        s_clause + " " + o_clause + " "
        "MATCH (s)<-[:SUBJECT]-(a:Assertion)-[:OBJECT]->(o) "
        "WHERE a.predicate = $predicate AND a.organization_id = $organization_id "
        "  AND NOT (a)-[:SUPERSEDED_BY]->() "
        "WITH s, o, a ORDER BY a.asserted_at DESC LIMIT 1 "
        f"MERGE (s)-[r:{rel_type} {{organization_id: $organization_id}}]->(o) "
        "ON CREATE SET r.confidence = a.confidence, r.asserted_at = a.asserted_at, "
        "              r.assertion_id = a.id, r.direction = a.direction, "
        "              r.source = 'provenance-spine' "
        "ON MATCH SET r.assertion_id = a.id, r.asserted_at = a.asserted_at, "
        "             r.confidence = a.confidence, r.direction = a.direction"
    )
    params = {
        **s_params, **o_params,
        "predicate": predicate, "organization_id": organization_id,
    }
    return stmt, params


def cypher_for_recorded_links(analysis_id: str, decision_ids: list[str],
                              organization_id: str) -> tuple[str, dict[str, Any]] | None:
    """#258: link an analysis to the decision(s) it recorded.

    MERGE (:Analysis)-[:RECORDED]->(:Decision) for each decision id, org-scoped
    (both nodes must share organization_id) and non-gating (an id with no
    matching :Decision in this database is a silent no-op via OPTIONAL MATCH +
    FOREACH). Returns None when there is nothing to link, so callers can skip the
    write entirely. Fully parameterized — no id/org is interpolated.
    """
    if not decision_ids:
        return None
    stmt = (
        "MATCH (an:Analysis {id: $aid, organization_id: $org}) "
        "UNWIND $dids AS did "
        "OPTIONAL MATCH (d:Decision {id: did, organization_id: $org}) "
        "FOREACH (_ IN CASE WHEN d IS NULL THEN [] ELSE [1] END | "
        "         MERGE (an)-[:RECORDED]->(d))"
    )
    return stmt, {"aid": analysis_id, "dids": list(decision_ids), "org": organization_id}


def write_finding(driver, rec: "FindingRecord", database: str = "neo4j",
                  link_decision_ids: list[str] | None = None) -> dict:
    """Execute the finding writes, then per assertion resolve endpoints, project the
    concrete edge (wired predicates only, and only when subject/object labels match
    the wired tuple), and report per-assertion outcomes."""
    statements = cypher_for_finding(rec)
    with driver.session(database=database) as session:
        for stmt, params in statements:
            session.execute_write(lambda tx, s=stmt, p=params: tx.run(s, p).consume())

        assertions_out = []
        resolved_count = unresolved_count = 0
        canon = canonical_orgs()
        for a in rec.assertions:
            # resolved iff both SUBJECT and OBJECT edges landed
            deg = session.execute_read(lambda tx, aid=a.id: tx.run(
                "MATCH (x:Assertion {id: $id}) "
                "RETURN size([(x)-[:SUBJECT]->() | 1]) AS s, size([(x)-[:OBJECT]->() | 1]) AS o",
                {"id": aid}).single())
            resolved = bool(deg["s"]) and bool(deg["o"])
            projected = False
            # Only project when the predicate is wired AND the assertion's
            # subject/object labels actually match the wired tuple — otherwise
            # a mismatched pair (e.g. (Disease)-[:PRODUCES]->(Taxon)) would
            # get materialized as a nonsensical concrete edge.
            labels_match = WIRED_PREDICATES.get(a.predicate) == (a.subject.label, a.object_.label)
            projection_kind = None
            if resolved and labels_match:
                # #297 Part B: both canonical AND non-canonical orgs project now,
                # via DIFFERENT paths. A canonical assertion updates the shared
                # reference edge (org-scoped so it never touches a customer edge);
                # a non-canonical (customer/eval) assertion projects a PARALLEL
                # edge keyed on its org, which edge-org-aware reads (#297 Part A)
                # scope to the owning org — so it never leaks cross-org (the #284
                # property is preserved by scoping, not by refusing to project).
                if is_canonical_org(a.organization_id, canon):
                    pstmt, pparams = cypher_for_projection(
                        a.predicate, a.subject, a.object_, a.organization_id, canon)
                    projection_kind = "canonical"
                else:
                    pstmt, pparams = cypher_for_parallel_projection(
                        a.predicate, a.subject, a.object_, a.organization_id)
                    projection_kind = "parallel"
                session.execute_write(lambda tx, s=pstmt, p=pparams: tx.run(s, p).consume())
                projected = True
            resolved_count += 1 if resolved else 0
            unresolved_count += 0 if resolved else 1
            row = {"id": a.id, "resolved": resolved,
                   "projected": projected, "superseded": a.supersedes}
            if projection_kind:
                row["projection_kind"] = projection_kind
            assertions_out.append(row)

        # #258: link this analysis to any decision(s) it recorded (non-gating,
        # org-scoped). Unknown/foreign ids are no-ops; report how many landed.
        recorded_links = 0
        link = cypher_for_recorded_links(
            rec.analysis.id, link_decision_ids or [], rec.analysis.organization_id)
        if link is not None:
            lstmt, lparams = link
            session.execute_write(lambda tx, s=lstmt, p=lparams: tx.run(s, p).consume())
            recorded_links = session.execute_read(lambda tx: tx.run(
                "MATCH (:Analysis {id: $aid, organization_id: $org})-[r:RECORDED]->(d:Decision) "
                "WHERE d.id IN $dids RETURN count(r) AS n",
                {"aid": rec.analysis.id, "org": rec.analysis.organization_id,
                 "dids": list(link_decision_ids or [])}).single()["n"])
    return {"experiment_id": rec.experiment.id, "analysis_id": rec.analysis.id,
            "resolved_count": resolved_count, "unresolved_count": unresolved_count,
            "recorded_links": recorded_links,
            "assertions": assertions_out}
