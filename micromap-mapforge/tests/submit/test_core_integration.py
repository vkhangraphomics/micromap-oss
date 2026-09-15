"""Integration test — requires Docker running."""

from pathlib import Path

import pytest
from neo4j import GraphDatabase

from micromap_mapforge.emit.bundle import IngestBundle
from micromap_mapforge.submit.core import MicroMapCoreExecutor


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
    url = neo4j_container.get_connection_url()
    drv = GraphDatabase.driver(url, auth=(neo4j_container.username, neo4j_container.password))
    yield drv
    drv.close()


def test_core_executor_round_trip(driver, tmp_path: Path):
    root = tmp_path / "out"
    (root / "cypher").mkdir(parents=True)
    (root / "cypher" / "nodes_Taxon.cypher").write_text(
        "MERGE (n:Taxon {ncbi_tax_id: '562', organization_id: 'test-org', scientific_name: 'Escherichia coli'});\n",
        encoding="utf-8",
    )

    bundle = IngestBundle(root=root)
    executor = MicroMapCoreExecutor(driver=driver, database="neo4j")
    receipt = executor.submit(bundle)

    assert receipt.success
    # Query back
    with driver.session() as session:
        result = session.run(
            "MATCH (n:Taxon {organization_id: 'test-org'}) RETURN n.ncbi_tax_id AS id, n.scientific_name AS name"
        )
        records = list(result)
        assert len(records) == 1
        assert records[0]["id"] == "562"
        assert records[0]["name"] == "Escherichia coli"
