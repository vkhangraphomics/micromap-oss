"""
Cross-reference validation for the MicroMap Knowledge Graph.

This module provides validation functions that check data consistency
across multiple data sources in the knowledge graph, identifying
orphaned nodes, ID conflicts, dangling relationships, and coverage gaps.
"""

import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Relationship types are interpolated into Cypher (they cannot be parameterised),
# so only plain identifiers are queried; anything else is skipped rather than
# escaped, keeping the generated Cypher readable.
_SAFE_REL_TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Node labels reported on by `validate_source_coverage`.
#
# This is a *reporting* list, not a guardrail — it selects what the coverage
# report covers, and cannot manufacture a false pass the way an enumerated
# guardrail can (see validate_orphaned_nodes / validate_null_organization_id,
# which ask the database instead). It was still wrong: `Metabolite` has had 0
# nodes since the #146 rename and `Compound` — the label 2,196 real nodes carry
# — was absent, so the coverage report silently omitted every metabolite. Same
# rot as #269/#298, milder blast radius.
#
# `Subject` is dropped: no loader writes it. `Sample` is kept — metabo_lights
# and pride write it, it is simply not in the loaded baseline.
EXPECTED_LABELS = [
    "Taxon", "Disease", "Compound", "Pathway", "Gene",
    "Drug", "Protein", "Study", "Sample", "Paper", "BodySite",
]

# Internal bookkeeping labels: schema metadata, never served by the API, and so
# correctly carry no `organization_id`. Excluded from the null-org check only.
#
# This is deliberately an EXCLUSION list, not an allowlist of data labels: any
# new/unknown label is checked by default (fail-safe). An inclusion list is what
# rotted and let #269 through — see validate_null_organization_id.
INTERNAL_LABELS = frozenset({"SchemaVersion"})

# Expected endpoint labels per relationship type: type -> (start, end).
#
# Declarative rather than five copy-pasted query blocks, so that
# `validate_relationship_rule_coverage` can cross-check every rule against the
# types the database actually holds. That guard is the point: the previous
# hand-written blocks named `CHILD_OF`, which no loader has ever written, so the
# taxonomy rule matched zero rows and passed unconditionally while `HAS_PARENT`
# (the real hierarchy, ~1.6M edges) went unchecked entirely (#298).
RELATIONSHIP_ENDPOINTS = {
    "ASSOCIATED_WITH_DISEASE": ("Taxon", "Disease"),
    "LINKED_TO_DISEASE": ("Compound", "Disease"),
    "PRODUCES": ("Taxon", "Compound"),
    "PARTICIPATES_IN": ("Compound", "Pathway"),
    "HAS_PARENT": ("Taxon", "Taxon"),
}


def _make_result(check: str, passed: bool, details, warnings: int = 0, errors: int = 0) -> Dict[str, Any]:
    """Build a standardized validation result dict."""
    return {
        "check": check,
        "passed": passed,
        "details": details,
        "warnings": warnings,
        "errors": errors,
    }


def validate_null_organization_id(driver, database: str) -> Dict[str, Any]:
    """
    Find nodes with no `organization_id` — they are invisible to every API caller.

    The shared+private read predicate
    `(n.organization_id = $organization_id OR n.organization_id IN $public_orgs)`
    is never true for a NULL property (Neo4j: `null = x` and `null IN [...]` are
    null, not false), so an unstamped node is present in the graph but absent
    from every REST/MCP read. That is a silent data-loss mode, hence errors
    rather than warnings.

    Deliberately label-agnostic: it asks the database which labels are offending
    instead of iterating a list like EXPECTED_LABELS. Both previous guardrails
    enumerated labels and both missed #269 because their lists had rotted past
    the #146 :Metabolite -> :Compound rename. A list can go stale; this cannot.

    INTERNAL_LABELS (e.g. :SchemaVersion) are excluded — they are schema
    bookkeeping the API never serves. That exclusion is negative and fail-safe:
    an unknown label is still reported.

    Backfill offenders with:
        MATCH (n) WHERE n.organization_id IS NULL SET n.organization_id = 'default'
    """
    check_name = "Nodes have organization_id (API visibility)"
    orgless_by_label: Dict[str, int] = {}

    with driver.session(database=database) as session:
        result = session.run(
            "MATCH (n) WHERE n.organization_id IS NULL "
            "AND NOT any(l IN labels(n) WHERE l IN $internal_labels) "
            "RETURN labels(n)[0] AS label, count(*) AS cnt ORDER BY cnt DESC",
            internal_labels=sorted(INTERNAL_LABELS),
        )
        for record in result:
            orgless_by_label[record["label"]] = record["cnt"]

    total = sum(orgless_by_label.values())

    if total == 0:
        details: Any = "No nodes with null organization_id"
    else:
        details = {
            "orgless_by_label": orgless_by_label,
            "total": total,
            "impact": (
                f"{total} node(s) are invisible to every API caller under the "
                f"org read predicate"
            ),
            "fix": (
                "MATCH (n) WHERE n.organization_id IS NULL "
                "SET n.organization_id = 'default'"
            ),
        }

    return _make_result(
        check=check_name,
        passed=total == 0,
        details=details,
        warnings=0,
        errors=total,
    )


def validate_orphaned_nodes(driver, database: str) -> Dict[str, Any]:
    """
    Find nodes with no relationships, grouped by label.

    Orphaned nodes may indicate failed relationship creation during ingestion
    or data that was loaded without being linked to the rest of the graph.

    Asks the database which labels have orphans rather than iterating
    EXPECTED_LABELS. That list still reads `Metabolite` (0 nodes since the #146
    rename) and has never held `Compound` or the derived `Gene` layer, so an
    orphaned `:Compound` was invisible to the check whose whole job is finding
    orphans (#298). Same rot as #269; same fix as #270 -- ask, don't declare.
    """
    check_name = "Orphaned nodes (no relationships)"
    orphaned_by_label: Dict[str, int] = {}

    with driver.session(database=database) as session:
        result = session.run(
            "MATCH (n) WHERE NOT (n)--() "
            "AND NOT any(l IN labels(n) WHERE l IN $internal_labels) "
            "RETURN labels(n)[0] AS label, count(*) AS cnt ORDER BY cnt DESC",
            internal_labels=sorted(INTERNAL_LABELS),
        )
        for record in result:
            orphaned_by_label[record["label"]] = record["cnt"]

    total_orphaned = sum(orphaned_by_label.values())

    if total_orphaned == 0:
        details: Any = "No orphaned nodes found"
    else:
        details = {
            "orphaned_by_label": orphaned_by_label,
            "total": total_orphaned,
        }

    return _make_result(
        check=check_name,
        passed=total_orphaned == 0,
        details=details,
        warnings=len(orphaned_by_label),
        errors=0,
    )


def validate_taxonomy_consistency(driver, database: str) -> Dict[str, Any]:
    """
    Check taxonomy data consistency.

    Verifies:
    - NCBI tax IDs are unique per Taxon node
    - No taxa share the same ncbi_tax_id with different taxon_id values
    """
    check_name = "Taxonomy ID consistency"
    issues: List[str] = []

    with driver.session(database=database) as session:
        # Check for duplicate ncbi_tax_id values across distinct Taxon nodes
        result = session.run("""
            MATCH (t:Taxon)
            WHERE t.ncbi_tax_id IS NOT NULL
            WITH t.ncbi_tax_id AS ncbi_id, collect(t.taxon_id) AS taxon_ids
            WHERE size(taxon_ids) > 1
            RETURN ncbi_id, taxon_ids
            LIMIT 50
        """)
        duplicates = list(result)
        if duplicates:
            issues.append(
                f"{len(duplicates)} NCBI tax IDs map to multiple Taxon nodes"
            )

        # Check for Taxon nodes missing ncbi_tax_id
        result = session.run("""
            MATCH (t:Taxon)
            WHERE t.ncbi_tax_id IS NULL
            RETURN count(t) AS cnt
        """)
        missing_count = result.single()["cnt"]
        if missing_count > 0:
            issues.append(f"{missing_count} Taxon nodes missing ncbi_tax_id")

    errors = len(duplicates) if duplicates else 0
    warnings = 1 if missing_count > 0 else 0

    if not issues:
        details = "All taxonomy IDs are consistent"
    else:
        details = "; ".join(issues)

    return _make_result(
        check=check_name,
        passed=errors == 0,
        details=details,
        warnings=warnings,
        errors=errors,
    )


def validate_metabolite_ids(driver, database: str) -> Dict[str, Any]:
    """
    Check HMDB, KEGG, and ChEBI ID consistency on Metabolite nodes.

    Verifies:
    - HMDB IDs follow the expected format (HMDB\\d+)
    - KEGG IDs follow the expected format (C\\d+)
    - Metabolites have at least one external ID
    """
    check_name = "Metabolite ID consistency"
    issues: List[str] = []
    warnings = 0
    errors = 0

    with driver.session(database=database) as session:
        # Check for metabolites with no external IDs at all
        result = session.run("""
            MATCH (m:Compound)
            WHERE m.hmdb_id IS NULL AND m.kegg_id IS NULL AND m.chebi_id IS NULL
            RETURN count(m) AS cnt
        """)
        no_ext_id = result.single()["cnt"]
        if no_ext_id > 0:
            issues.append(f"{no_ext_id} metabolites have no external ID (HMDB/KEGG/ChEBI)")
            warnings += 1

        # Check HMDB ID format
        result = session.run("""
            MATCH (m:Compound)
            WHERE m.hmdb_id IS NOT NULL AND NOT m.hmdb_id =~ 'HMDB\\\\d+'
            RETURN count(m) AS cnt
        """)
        bad_hmdb = result.single()["cnt"]
        if bad_hmdb > 0:
            issues.append(f"{bad_hmdb} metabolites have malformed HMDB IDs")
            errors += 1

        # Check KEGG ID format
        result = session.run("""
            MATCH (m:Compound)
            WHERE m.kegg_id IS NOT NULL AND NOT m.kegg_id =~ 'C\\\\d+'
            RETURN count(m) AS cnt
        """)
        bad_kegg = result.single()["cnt"]
        if bad_kegg > 0:
            issues.append(f"{bad_kegg} metabolites have malformed KEGG IDs")
            errors += 1

        # Check for duplicate HMDB IDs
        result = session.run("""
            MATCH (m:Compound)
            WHERE m.hmdb_id IS NOT NULL
            WITH m.hmdb_id AS hid, count(*) AS cnt
            WHERE cnt > 1
            RETURN count(*) AS dup_count
        """)
        dup_hmdb = result.single()["dup_count"]
        if dup_hmdb > 0:
            issues.append(f"{dup_hmdb} duplicate HMDB IDs found")
            errors += 1

    if not issues:
        details = "All metabolite IDs are consistent"
    else:
        details = "; ".join(issues)

    return _make_result(
        check=check_name,
        passed=errors == 0,
        details=details,
        warnings=warnings,
        errors=errors,
    )


def validate_dangling_relationships(driver, database: str) -> Dict[str, Any]:
    """
    Find relationships where start or end node labels are unexpected.

    In a well-formed graph, relationships should connect nodes of expected
    label combinations. This check looks for relationships whose endpoints
    may have been deleted or were never properly created.
    """
    check_name = "Dangling / unexpected relationships"
    issues: List[str] = []

    with driver.session(database=database) as session:
        for rel_type, (start_label, end_label) in RELATIONSHIP_ENDPOINTS.items():
            result = session.run(f"""
                MATCH (a)-[r:{rel_type}]->(b)
                WHERE NOT (a:{start_label}) OR NOT (b:{end_label})
                RETURN count(r) AS cnt
            """)
            bad = result.single()["cnt"]
            if bad > 0:
                issues.append(
                    f"{bad} {rel_type} rels with unexpected endpoint labels "
                    f"(expected (:{start_label})->(:{end_label}))"
                )

    errors = len(issues)
    if not issues:
        details = "All relationships have expected endpoint labels"
    else:
        details = "; ".join(issues)

    return _make_result(
        check=check_name,
        passed=errors == 0,
        details=details,
        warnings=0,
        errors=errors,
    )


def validate_relationship_rule_coverage(
    driver, database: str, rules: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Cross-check every endpoint rule against the relationship types that exist.

    A rule naming a type the graph does not hold is worse than no rule: it
    matches zero rows, so it reports green forever and nothing ever notices the
    guardrail is disarmed. That is exactly how `CHILD_OF` hid the fact that
    `HAS_PARENT` — two thirds of all edges — was unvalidated (#298), and how a
    hardcoded label list hid the org-less nodes in #269.

    Correcting the literal alone would rot again on the next rename, so this
    asks the database which types exist and reports both directions:

    - a declared rule matching no edges -> ERROR (the check is vacuous)
    - a type in the graph with no rule   -> WARNING (unvalidated, as HAS_PARENT was)
    """
    check_name = "Relationship rule coverage"
    rules = RELATIONSHIP_ENDPOINTS if rules is None else rules

    with driver.session(database=database) as session:
        record = session.run(
            "CALL db.relationshipTypes() YIELD relationshipType "
            "RETURN collect(relationshipType) AS types"
        ).single()
        types_in_graph = set(record["types"] or [])

        # The type token can linger after the last edge is deleted (as the
        # :Metabolite label still does post-#146), so confirm by count rather
        # than trusting the registry.
        present = set()
        for rel_type in sorted(set(rules) | types_in_graph):
            if not _SAFE_REL_TYPE.match(rel_type):
                continue
            cnt = session.run(
                f"MATCH ()-[r:{rel_type}]->() RETURN count(r) AS cnt"
            ).single()["cnt"]
            if cnt > 0:
                present.add(rel_type)

    # A freshly-initialised database holds no edges, so every rule is trivially
    # unmatched. Failing there would fire on every new DB and train people to
    # ignore the check -- the :SchemaVersion lesson from #270.
    if not present:
        return _make_result(
            check=check_name,
            passed=True,
            details="Graph holds no relationships; no rules to cross-check",
        )

    vacuous = sorted(set(rules) - present)
    unruled = sorted(present - set(rules))

    parts: List[str] = []
    if vacuous:
        parts.append(
            f"{len(vacuous)} endpoint rule(s) match no relationship here and so "
            f"can never fail: {', '.join(vacuous)}. Either that source is not "
            f"loaded, or the rule names a type nothing writes (#298)."
        )
    if unruled:
        parts.append(
            f"{len(unruled)} relationship type(s) have no endpoint rule and go "
            f"unvalidated: {', '.join(unruled)}"
        )

    # Advisory only. On a partially loaded database a live rule legitimately
    # matches nothing, so failing here would fire on every dev box until people
    # learned to ignore it (#270's :SchemaVersion lesson). The deterministic
    # guard against a rule naming a type NO loader writes is the static test in
    # tests/test_validation_relationship_rules.py, which needs no database.
    return _make_result(
        check=check_name,
        passed=True,
        details={
            "vacuous_rules": vacuous,
            "unruled_types": unruled,
            "message": "; ".join(parts) if parts
                       else "Every endpoint rule matches a live relationship type",
        },
        warnings=len(vacuous) + (1 if unruled else 0),
    )


def validate_source_coverage(driver, database: str) -> Dict[str, Any]:
    """
    Check which data sources contributed to which node types.

    Returns a mapping of node labels to the set of source values found,
    helping identify gaps where a data source may not have loaded correctly.
    """
    check_name = "Source coverage by node type"
    coverage: Dict[str, list] = {}
    warnings = 0

    with driver.session(database=database) as session:
        for label in EXPECTED_LABELS:
            # Try common source property names
            result = session.run(f"""
                MATCH (n:{label})
                WHERE n.source IS NOT NULL
                RETURN DISTINCT n.source AS src
                UNION
                MATCH (n:{label})
                WHERE n.sources IS NOT NULL
                UNWIND
                    CASE
                        WHEN n.sources IS NOT NULL AND size(n.sources) > 0 THEN n.sources
                        ELSE [n.sources]
                    END AS src
                RETURN DISTINCT src
            """)
            sources = sorted({r["src"] for r in result if r["src"] is not None})
            if sources:
                coverage[label] = sources

        # Check for labels with no source tracking at all
        labels_without_sources = []
        for label in EXPECTED_LABELS:
            result = session.run(f"MATCH (n:{label}) RETURN count(n) AS cnt")
            count = result.single()["cnt"]
            if count > 0 and label not in coverage:
                labels_without_sources.append(label)

    if labels_without_sources:
        warnings = len(labels_without_sources)

    details = {
        "source_coverage": coverage,
    }
    if labels_without_sources:
        details["labels_without_source_tracking"] = labels_without_sources

    return _make_result(
        check=check_name,
        passed=True,  # informational check
        details=details,
        warnings=warnings,
        errors=0,
    )


# Every check run by `--validate` / `--validate-xrefs`, in report order.
# Exported so callers and tests can derive the count instead of hardcoding it.
CHECKS = [
    validate_null_organization_id,
    validate_orphaned_nodes,
    validate_taxonomy_consistency,
    validate_metabolite_ids,
    validate_dangling_relationships,
    validate_relationship_rule_coverage,
    validate_source_coverage,
]


def run_cross_reference_validation(driver, database: str) -> Dict[str, Any]:
    """
    Run all cross-reference validation checks and return a summary.

    Returns a dict with:
    - results: list of individual check results
    - summary: counts of passed/failed/warnings/errors
    """
    logger.info("Running cross-reference validation...")

    checks = CHECKS

    results: List[Dict[str, Any]] = []
    total_warnings = 0
    total_errors = 0
    total_passed = 0
    total_failed = 0

    for check_fn in checks:
        try:
            result = check_fn(driver, database)
            results.append(result)
            total_warnings += result["warnings"]
            total_errors += result["errors"]
            if result["passed"]:
                total_passed += 1
            else:
                total_failed += 1
        except Exception as e:
            logger.exception("Validation check %s failed with error: %s", check_fn.__name__, e)
            results.append(_make_result(
                check=check_fn.__name__,
                passed=False,
                details=f"Check failed with error: {e}",
                warnings=0,
                errors=1,
            ))
            total_failed += 1
            total_errors += 1

    summary = {
        "total_checks": len(results),
        "passed": total_passed,
        "failed": total_failed,
        "warnings": total_warnings,
        "errors": total_errors,
    }

    # Print results
    print("\n" + "=" * 60)
    print("CROSS-REFERENCE VALIDATION RESULTS")
    print("=" * 60)

    for r in results:
        status = "[PASS]" if r["passed"] else "[FAIL]"
        extra = ""
        if r["warnings"] > 0:
            extra += f" ({r['warnings']} warnings)"
        if r["errors"] > 0:
            extra += f" ({r['errors']} errors)"
        print(f"\n{status}: {r['check']}{extra}")
        if isinstance(r["details"], dict):
            for k, val in r["details"].items():
                if isinstance(val, dict):
                    print(f"   {k}:")
                    for kk, vv in val.items():
                        print(f"      {kk}: {vv}")
                elif isinstance(val, list):
                    print(f"   {k}: {', '.join(str(v) for v in val)}")
                else:
                    print(f"   {k}: {val}")
        else:
            print(f"   {r['details']}")

    print("\n" + "-" * 60)
    print(f"SUMMARY: {total_passed} passed, {total_failed} failed, "
          f"{total_warnings} warnings, {total_errors} errors")
    print("=" * 60 + "\n")

    return {
        "results": results,
        "summary": summary,
    }
