"""#259: prove endpoint resolution is index-eligible.

Confirms with a real planner (EXPLAIN) that an allowlisted (label, key_field)
endpoint resolves via a NodeIndexSeek, while an unlisted pair still resolves via
an AllNodesScan (the resolve-if-it-exists fallback). Requires a live Neo4j.
"""
import os
import pytest
from neo4j import GraphDatabase

from micromap_mapforge.provenance.spine import EndpointRef, _endpoint_clause

DB = os.environ.get("NEO4J_DATABASE", "neo4j")

# Needs a live Neo4j; runs only in the integration job (via testcontainers,
# like the other DB integration tests), skips if Docker/testcontainers is
# unavailable, and is excluded from the unit job. See #288.
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def neo4j_container():
    try:
        from testcontainers.neo4j import Neo4jContainer
    except ImportError:
        pytest.skip("testcontainers[neo4j] not installed")
    try:
        with Neo4jContainer("neo4j:5.15") as n4j:
            yield n4j
    except Exception as e:
        pytest.skip(f"Could not start Neo4j container: {e}")


@pytest.fixture(scope="module")
def driver(neo4j_container):
    d = GraphDatabase.driver(
        neo4j_container.get_connection_url(),
        auth=(neo4j_container.username, neo4j_container.password),
    )
    # The allowlisted Taxon/ncbi_tax_id pair is only index-eligible if the index
    # actually exists — mirror what neo4j_schema.py declares so the test is
    # self-contained on a fresh database.
    with d.session(database=DB) as s:
        s.run("CREATE INDEX taxon_ncbi_tax_id IF NOT EXISTS FOR (t:Taxon) ON (t.ncbi_tax_id)")
        s.run("CALL db.awaitIndexes(300)")
    yield d
    d.close()


def _operators(driver, cypher, params):
    """Flatten every operatorType in the query's execution plan (via EXPLAIN)."""
    with driver.session(database=DB) as s:
        plan = s.run(cypher, params).consume().plan
    ops, stack = [], [plan]
    while stack:
        node = stack.pop()
        if not node:
            continue
        # operatorType comes back annotated with the planner/runtime, e.g.
        # "NodeIndexSeek@neo4j" — strip the suffix to compare on the bare name.
        ops.append(node.get("operatorType", "").split("@", 1)[0])
        stack.extend(node.get("children") or [])
    return ops


@pytest.mark.parametrize("match_kw", ["MATCH", "OPTIONAL MATCH"])
def test_allowlisted_endpoint_plans_index_seek(driver, match_kw):
    # Both the projection (MATCH) and finding (OPTIONAL MATCH) paths must seek.
    clause, params = _endpoint_clause(
        match_kw, "n", EndpointRef("Taxon", "ncbi_tax_id", "209879"), "ep")
    ops = _operators(driver, f"EXPLAIN {clause} RETURN n", params)
    assert "NodeIndexSeek" in ops, ops
    assert "AllNodesScan" not in ops, ops


def test_unlisted_endpoint_plans_all_nodes_scan(driver):
    # Contrast case: a pair with no matching index still resolves — via a full
    # scan. This documents the fallback and proves the detector can see a scan,
    # so the seek assertion above is meaningful.
    clause, params = _endpoint_clause(
        "MATCH", "n", EndpointRef("Widget", "widget_id", "w1"), "ep")
    ops = _operators(driver, f"EXPLAIN {clause} RETURN n", params)
    assert "AllNodesScan" in ops, ops
    assert "NodeIndexSeek" not in ops, ops
