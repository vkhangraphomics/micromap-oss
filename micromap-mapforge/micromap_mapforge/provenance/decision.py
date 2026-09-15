# micromap-mapforge/micromap_mapforge/provenance/decision.py
"""Decision provenance node: the *decision tier* over a contribution's *execution tier*.

When `mapforge submit` writes data + a :Contribution, it can also record a
:Decision{action_type:'data_contribution'} so the upload appears, live and cited,
in MicroMap's decision-provenance ledger (issue #190). This mirrors the tiered
decision/execution provenance from the federation paper (arXiv:2605.23985):

    (:Decision {id, tool:'mapforge', action_type:'data_contribution', ...})
        -[:RECORDS]-> (:Contribution)              // decision -> execution provenance
        -[:ABOUT]->   (:Disease|:Taxon|...)         // grounded biology (the subject)

NON-GATING: the caller invokes write_decision() in a try/except so a provenance
failure never blocks the data write to a MapForge KG. Like contribution.py, every
statement is parameterized — no value is interpolated into the Cypher string, so a
crafted entity name / source id cannot fragment a statement.

ABOUT edges come from two sources:
  - `entity_tags`  : agent-supplied canonical tags (the curated subject). A tag is
                     matched as a Disease name_normalized, or a Taxon taxon_id /
                     ncbi_tax_id — the same heuristic the #190 API route + seed use.
  - `about_resolved`: auto-derived from the bundle's resolution.json. Each entry is
                     {label, field, value} (the resolver's merge_field/merge_value),
                     matched field-accurately so the edge lands on exactly the node
                     the data attached to, regardless of MicroMap's heterogeneous
                     Taxon keys (taxon_id vs ncbi_tax_id vs gtdb_id).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class DecisionRecord:
    id: str                              # stable, idempotent (one per contribution)
    organization_id: str
    occurred_at: datetime
    summary: str
    tool: str = "mapforge"
    action_type: str = "data_contribution"
    rationale: str = ""
    actor_user: str = ""
    actor_org: str = ""
    role: str = "service"
    evidence_refs: list[str] = field(default_factory=list)

    # Curated subject tags (agent-supplied, e.g. via `submit --about`).
    entity_tags: list[str] = field(default_factory=list)

    # Auto-derived subjects from resolution.json: each {label, field, value}.
    about_resolved: list[dict[str, str]] = field(default_factory=list)

    # Link to the execution-tier :Contribution (its MERGE key). When both are
    # set, a (:Decision)-[:RECORDS]->(:Contribution) edge is written.
    contribution_mapping_sha256: str | None = None
    contribution_source_sha256: str | None = None

    # Magnitude of the contribution, carried onto the decision for the ledger.
    resolved_count: int = 0
    unresolved_count: int = 0
    ambiguous_count: int = 0


def cypher_for_decision(rec: DecisionRecord) -> list[tuple[str, dict[str, Any]]]:
    """Return a list of (cypher_statement, params) tuples. All values are $param
    bindings — nothing is interpolated into the Cypher string."""
    occurred_at_iso = rec.occurred_at.isoformat()
    stmts: list[tuple[str, dict[str, Any]]] = [
        (
            "MERGE (d:Decision {id: $id}) "
            "SET d.tool = $tool, d.action_type = $action_type, "
            "    d.occurred_at = $occurred_at, d.summary = $summary, "
            "    d.rationale = $rationale, d.evidence_refs = $evidence_refs, "
            "    d.actor_user = $actor_user, d.actor_org = $actor_org, d.role = $role, "
            "    d.organization_id = $organization_id, "
            "    d.resolved_count = $resolved_count, "
            "    d.unresolved_count = $unresolved_count, "
            "    d.ambiguous_count = $ambiguous_count",
            {
                "id": rec.id, "tool": rec.tool, "action_type": rec.action_type,
                "occurred_at": occurred_at_iso, "summary": rec.summary,
                "rationale": rec.rationale, "evidence_refs": rec.evidence_refs,
                "actor_user": rec.actor_user, "actor_org": rec.actor_org, "role": rec.role,
                "organization_id": rec.organization_id,
                "resolved_count": rec.resolved_count,
                "unresolved_count": rec.unresolved_count,
                "ambiguous_count": rec.ambiguous_count,
            },
        ),
    ]

    # decision -> execution provenance (link to the :Contribution).
    if rec.contribution_mapping_sha256 and rec.contribution_source_sha256:
        stmts.append((
            "MATCH (d:Decision {id: $id}), "
            "      (c:Contribution {mapping_sha256: $mapping_sha256, "
            "                       source_sha256: $source_sha256, "
            "                       organization_id: $organization_id}) "
            "MERGE (d)-[:RECORDS]->(c)",
            {
                "id": rec.id,
                "mapping_sha256": rec.contribution_mapping_sha256,
                "source_sha256": rec.contribution_source_sha256,
                "organization_id": rec.organization_id,
            },
        ))

    # ABOUT edges from curated canonical tags (Disease name_normalized, or
    # Taxon taxon_id / ncbi_tax_id). OPTIONAL MATCH so an unknown tag is a no-op,
    # not an error — the decision is still recorded.
    if rec.entity_tags:
        stmts.append((
            "MATCH (d:Decision {id: $id}) "
            "UNWIND $entity_tags AS tag "
            "OPTIONAL MATCH (e) "
            "  WHERE (e:Disease AND e.name_normalized = toLower(tag)) "
            "     OR (e:Taxon AND (e.taxon_id = tag OR e.ncbi_tax_id = tag)) "
            "FOREACH (_ IN CASE WHEN e IS NULL THEN [] ELSE [1] END | "
            "         MERGE (d)-[:ABOUT]->(e))",
            {"id": rec.id, "entity_tags": rec.entity_tags},
        ))

    # ABOUT edges from auto-derived resolved subjects, matched field-accurately.
    # Label can't be a Cypher parameter, so filter via labels(e); field via e[$f].
    if rec.about_resolved:
        stmts.append((
            "MATCH (d:Decision {id: $id}) "
            "UNWIND $about_resolved AS row "
            "OPTIONAL MATCH (e) "
            "  WHERE row.label IN labels(e) AND e[row.field] = row.value "
            "FOREACH (_ IN CASE WHEN e IS NULL THEN [] ELSE [1] END | "
            "         MERGE (d)-[:ABOUT]->(e))",
            {"id": rec.id, "about_resolved": rec.about_resolved},
        ))

    return stmts


def write_decision(driver, record: DecisionRecord, database: str = "neo4j") -> int:
    """Execute the parameterized Decision statements. Returns the number of
    :ABOUT edges now attached to the decision (for caller feedback). Raises on
    Neo4j errors — the CALLER is responsible for the non-gating try/except."""
    statements = cypher_for_decision(record)
    with driver.session(database=database) as session:
        for stmt, params in statements:
            session.execute_write(lambda tx, s=stmt, p=params: tx.run(s, p).consume())
        count = session.execute_read(
            lambda tx: tx.run(
                "MATCH (:Decision {id: $id})-[r:ABOUT]->() RETURN count(r) AS n",
                {"id": record.id},
            ).single()["n"]
        )
    return count
