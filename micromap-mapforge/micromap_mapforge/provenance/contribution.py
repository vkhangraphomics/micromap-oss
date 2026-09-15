# micromap-mapforge/micromap_mapforge/provenance/contribution.py
"""Contribution provenance node: captures who contributed what, when, and where it went.

Written to the destination's Neo4j (the same driver `mapforge submit` used).
For federated destinations this is the *remote* instance, not the hub — see
`new-instance-executor-contract.md` §"Provenance — two layers". An earlier
revision of this docstring claimed writes always went to MicroMap core; that
was stale relative to the executor contract.

Skipped entirely when `routing.yaml::provenance.enabled` is False — used for
stand-alone curated KGs that don't want :Organization/:Reviewer/:Contribution
nodes. See `docs/new-instance-executor-contract.md` §"Stand-alone curated KGs
— provenance opt-out" and issue #63 for the rationale.

Schema:

    (:Organization {id})-[:CONTRIBUTED]->(:Contribution)
    (:Reviewer {name})<-[:APPROVED_BY]-(:Contribution)
    (:Contribution {mapping_sha256, source_sha256, destination, submitted_at,
                    resolved_count, unresolved_count, ambiguous_count,
                    organization_id})

All writes use parameterized queries — values are passed as $param bindings,
never interpolated into the Cypher string. This closes the injection vector
where a semicolon in a reviewer name or org id could fragment a statement.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class ContributionRecord:
    contributor: str               # organization id of the contributor
    reviewer: str                  # reviewer identity (from manifest)
    organization_id: str           # multi-tenant isolation tag
    source_sha256: str             # hex digest of the source data file
    mapping_sha256: str            # hex digest of mapping.yaml
    destination: str               # "micromap-core" | "registry-only" | "new-federated-instance"
    submitted_at: datetime
    resolved_count: int
    unresolved_count: int
    ambiguous_count: int

    # E3 (#78) — source attribution. All optional; default None. When None,
    # cypher_for_contribution omits the field from the SET map entirely
    # (not SET to null). Pre-E3 bundles produce :Contribution nodes
    # without these properties, exactly as before E3.
    source_name: str | None = None
    source_license: str | None = None
    source_url: str | None = None
    source_doi: str | None = None
    source_contact: str | None = None
    source_version: str | None = None
    source_accessed_at: str | None = None  # ISO date string YYYY-MM-DD
    source_ethics_ref: str | None = None

    # E4 (#78) — governance/audit. Sparse semantics:
    #   True  -> bundle was unapproved AND --force was used (actual bypass)
    #   None  -> bundle was approved (with or without --force flag); no bypass
    #   False -> not produced by the CLI today. Accepted by the dataclass
    #            (the field type is `bool | None`) but has no defined production
    #            semantic. NOTE: False is NOT stripped by the Cypher writer
    #            (only None is filtered via `is not None`); a caller passing
    #            False would write `force_submitted: false` to the graph,
    #            which carries no documented meaning. Do not use.
    force_submitted: bool | None = None


# Allowlist of source-attribution fields permitted in the Contribution
# MERGE+SET statement. The Cypher SET clause is built dynamically from
# THIS LIST ONLY -- no user input flows into Cypher string concatenation.
# Values still flow through parameter bindings.
#
# Naming note: most fields here were introduced by E3 (#78), but
# `source_name` is the always-present source identifier (predates E3 in
# mapping.yaml::source, just wasn't previously threaded onto the
# :Contribution node). The constant name avoids "E3" to prevent the
# misleading suggestion that source_name is an E3-new field.
_SOURCE_ATTRIBUTION_FIELDS: tuple[str, ...] = (
    "source_name",
    "source_license",
    "source_url",
    "source_doi",
    "source_contact",
    "source_version",
    "source_accessed_at",
    "source_ethics_ref",
)


# Allowlist of governance/audit fields permitted in the Contribution
# MERGE+SET statement. Separate from _SOURCE_ATTRIBUTION_FIELDS because
# these fields describe HOW the submission happened (audit trail), not
# WHAT the source IS. The two-tuple split is a semantic-clarity choice —
# behaviorally, the SET clause treats both lists identically.
#
# E4 (#78) adds `force_submitted`. Future E4-adjacent fields
# (`force_submitted_reason`, `submitted_via_mcp`, etc.) would land here.
_GOVERNANCE_FIELDS: tuple[str, ...] = (
    "force_submitted",
)


def _build_contribution_merge(
    rec: "ContributionRecord", submitted_at_iso: str,
) -> tuple[str, dict[str, Any]]:
    """Build the Contribution MERGE+SET tuple.

    The SET map is constructed dynamically so that optional fields appear
    ONLY when present (not None). Two field categories share this sparse-
    write pattern: E3 source-attribution fields (`_SOURCE_ATTRIBUTION_FIELDS`)
    and E4 governance fields (`_GOVERNANCE_FIELDS`). Both are iterated under
    the same None-stripping logic — the two-tuple split is a semantic-
    clarity choice (attribution vs. audit), not a behavioral one.

    Cypher's `SET c += $map` semantics overwrite present keys and ignore
    absent keys, so the natural approach is to filter None values in
    Python before building the params.

    The MERGE key {mapping_sha256, source_sha256, organization_id} is
    unchanged from pre-E3 — E3 and E4 fields are SET-only, not MERGE-key.
    """
    # Always-present fields.
    base_assignments = [
        "destination: $destination",
        "submitted_at: $submitted_at",
        "resolved_count: $resolved_count",
        "unresolved_count: $unresolved_count",
        "ambiguous_count: $ambiguous_count",
    ]
    base_params: dict[str, Any] = {
        "mapping_sha256": rec.mapping_sha256,
        "source_sha256": rec.source_sha256,
        "organization_id": rec.organization_id,
        "destination": rec.destination,
        "submitted_at": submitted_at_iso,
        "resolved_count": rec.resolved_count,
        "unresolved_count": rec.unresolved_count,
        "ambiguous_count": rec.ambiguous_count,
    }

    # Source-attribution + governance fields: include in SET map ONLY
    # when not None. The two allowlists (_SOURCE_ATTRIBUTION_FIELDS and
    # _GOVERNANCE_FIELDS) are the ONLY sources of keys in the SET clause;
    # no user input flows into the Cypher string. The split is semantic
    # (attribution vs. audit); the SET-construction logic is uniform.
    for field in (*_SOURCE_ATTRIBUTION_FIELDS, *_GOVERNANCE_FIELDS):
        value = getattr(rec, field)
        if value is not None:
            base_assignments.append(f"{field}: ${field}")
            base_params[field] = value

    set_clause = ", ".join(base_assignments)
    cypher = (
        "MERGE (c:Contribution {"
        "mapping_sha256: $mapping_sha256, "
        "source_sha256: $source_sha256, "
        "organization_id: $organization_id}) "
        f"SET c += {{{set_clause}}}"
    )
    return (cypher, base_params)


def cypher_for_contribution(rec: ContributionRecord) -> list[tuple[str, dict[str, Any]]]:
    """Return a list of (cypher_statement, params_dict) tuples.

    Each statement uses only $param bindings — no value is interpolated
    into the Cypher string. Callers execute the tuples with
    `tx.run(stmt, **params)` (or `tx.run(stmt, params)`).
    """
    submitted_at_iso = rec.submitted_at.isoformat()
    return [
        (
            "MERGE (org:Organization {id: $contributor})",
            {"contributor": rec.contributor},
        ),
        (
            "MERGE (rev:Reviewer {name: $reviewer})",
            {"reviewer": rec.reviewer},
        ),
        _build_contribution_merge(rec, submitted_at_iso),
        (
            "MATCH (org:Organization {id: $contributor}), "
            "(c:Contribution {mapping_sha256: $mapping_sha256, "
            "source_sha256: $source_sha256, "
            "organization_id: $organization_id}) "
            "MERGE (org)-[:CONTRIBUTED]->(c)",
            {
                "contributor": rec.contributor,
                "mapping_sha256": rec.mapping_sha256,
                "source_sha256": rec.source_sha256,
                "organization_id": rec.organization_id,
            },
        ),
        (
            "MATCH (rev:Reviewer {name: $reviewer}), "
            "(c:Contribution {mapping_sha256: $mapping_sha256, "
            "source_sha256: $source_sha256, "
            "organization_id: $organization_id}) "
            "MERGE (c)-[:APPROVED_BY]->(rev)",
            {
                "reviewer": rec.reviewer,
                "mapping_sha256": rec.mapping_sha256,
                "source_sha256": rec.source_sha256,
                "organization_id": rec.organization_id,
            },
        ),
    ]


def write_contribution(driver, record: ContributionRecord, database: str = "neo4j") -> None:
    """Execute the parameterized Contribution statements via the Neo4j driver."""
    statements = cypher_for_contribution(record)
    with driver.session(database=database) as session:
        for stmt, params in statements:
            session.execute_write(lambda tx, s=stmt, p=params: tx.run(s, p).consume())
