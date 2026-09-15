"""Integration test — requires Docker running."""

from datetime import datetime, timezone

import pytest
from neo4j import GraphDatabase

from micromap_mapforge.provenance.contribution import (
    ContributionRecord,
    write_contribution,
)


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


@pytest.fixture
def driver(neo4j_container):
    drv = GraphDatabase.driver(
        neo4j_container.get_connection_url(),
        auth=(neo4j_container.username, neo4j_container.password),
    )
    yield drv
    drv.close()


def test_contribution_round_trip(driver):
    record = ContributionRecord(
        contributor="partner-xyz",
        reviewer="alice",
        organization_id="partner-xyz",
        source_sha256="feed1",
        mapping_sha256="feed2",
        destination="micromap-core",
        submitted_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
        resolved_count=10,
        unresolved_count=1,
        ambiguous_count=0,
    )
    write_contribution(driver, record, database="neo4j")

    with driver.session() as session:
        result = session.run(
            "MATCH (org:Organization {id: 'partner-xyz'})-[:CONTRIBUTED]->(c:Contribution) "
            "MATCH (c)-[:APPROVED_BY]->(rev:Reviewer) "
            "RETURN c.resolved_count AS resolved, c.destination AS dest, rev.name AS reviewer"
        )
        records = list(result)
        assert len(records) == 1
        assert records[0]["resolved"] == 10
        assert records[0]["dest"] == "micromap-core"
        assert records[0]["reviewer"] == "alice"
