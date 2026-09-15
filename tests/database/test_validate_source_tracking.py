"""#345: the `--validate` source-tracking check must recognize BOTH forms of
source tracking on an ASSOCIATED_WITH_DISEASE edge.

Ingestion loaders (Disbiome/GMRepo) set `r.sources` (plural list); the provenance
spine sets `r.source = 'provenance-spine'` (singular), including the #297B
parallel per-org projections. The old check tested only `r.sources IS NULL`, so
it flagged every spine-projected edge as "missing source tracking" — a false
alarm that grows with every derive and every customer contribution.

Runs against a throwaway Neo4j (seeded_neo4j_env, #307) — a mock can't catch a
Cypher WHERE-clause semantics bug, so this seeds real edges and runs the real
query the validator uses.
"""
import os

import pytest
from neo4j import GraphDatabase

from database.load_knowledge_graph import REL_SOURCE_TRACKING_QUERY

pytestmark = [pytest.mark.integration, pytest.mark.seeded]


@pytest.fixture()
def driver(seeded_neo4j_env):
    d = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    )
    yield d
    d.close()


def _seed_edges(session):
    session.run("MATCH (n) DETACH DELETE n")
    session.run(
        """
        CREATE (t1:Taxon {name:'spine'})-[:ASSOCIATED_WITH_DISEASE {source:'provenance-spine'}]->(d1:Disease {name:'d1'})
        CREATE (t2:Taxon {name:'ingested'})-[:ASSOCIATED_WITH_DISEASE {sources:['disbiome']}]->(d2:Disease {name:'d2'})
        CREATE (t3:Taxon {name:'untracked'})-[:ASSOCIATED_WITH_DISEASE]->(d3:Disease {name:'d3'})
        """
    )


def test_only_the_untracked_edge_is_flagged(driver):
    db = os.environ.get("NEO4J_DATABASE", "neo4j")
    with driver.session(database=db) as s:
        _seed_edges(s)
        count = s.run(REL_SOURCE_TRACKING_QUERY).single()["count"]
    # The spine edge (r.source) and the ingestion edge (r.sources) are both
    # tracked; only the third edge, carrying neither, is missing tracking.
    assert count == 1, (
        f"expected only the 1 truly-untracked edge to be flagged, got {count} "
        f"— a spine edge (r.source='provenance-spine') is being counted as "
        f"untracked because the check ignores the singular property"
    )


def test_a_fully_tracked_graph_reports_zero(driver):
    db = os.environ.get("NEO4J_DATABASE", "neo4j")
    with driver.session(database=db) as s:
        s.run("MATCH (n) DETACH DELETE n")
        s.run(
            "CREATE (:Taxon)-[:ASSOCIATED_WITH_DISEASE {source:'provenance-spine'}]->(:Disease)"
        )
        count = s.run(REL_SOURCE_TRACKING_QUERY).single()["count"]
    assert count == 0, "a spine-only graph must pass source tracking, not flag every edge"
