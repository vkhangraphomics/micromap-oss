import os
import pytest
from neo4j import GraphDatabase
from database.neo4j_schema import create_provenance_spine_schema

# Runs against a throwaway Neo4j provisioned by `seeded_neo4j_env` (#307).
# It previously opened bolt://localhost:7687 unconditionally and raised
# ConnectionRefusedError with no box present — a hard failure indistinguishable
# from a real regression, and the reason `-m integration` could not run in CI.
# It needs only an EMPTY database (it creates the schema it asserts on), so a
# container serves it exactly as well as a live box and works everywhere.
pytestmark = [pytest.mark.integration, pytest.mark.seeded]

@pytest.fixture(scope="module")
def driver(seeded_neo4j_env):
    d = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    )
    yield d
    d.close()

def test_spine_constraints_created(driver):
    db = os.environ.get("NEO4J_DATABASE", "neo4j")
    with driver.session(database=db) as s:
        create_provenance_spine_schema(s)
    # constraint names are implementation-defined; assert the labels are constrained
    with driver.session(database=db) as s:
        cons = list(s.run("SHOW CONSTRAINTS YIELD labelsOrTypes, properties RETURN labelsOrTypes, properties"))
    pairs = {(tuple(c["labelsOrTypes"]), tuple(c["properties"])) for c in cons}
    assert (("Assertion",), ("id",)) in pairs
    assert (("Experiment",), ("id",)) in pairs
    assert (("Analysis",), ("id",)) in pairs
    # #311 — bare `id`, matching `MERGE (d:Decision {id: $id})` in both writers.
    assert (("Decision",), ("id",)) in pairs


def test_provenance_constraints_are_creatable_in_real_neo4j(driver):
    """#311: the unit tests drive a MagicMock, which accepts any string as
    Cypher. Only a real server proves the constraint syntax parses and that the
    composite :Contribution key is accepted."""
    from database.load_knowledge_graph import create_indexes_and_constraints

    db = os.environ.get("NEO4J_DATABASE", "neo4j")
    report = create_indexes_and_constraints(driver, db)
    assert report["constraints_failed"] == 0, (
        "a constraint was rejected by a real Neo4j — check the ERROR log above"
    )

    with driver.session(database=db) as s:
        cons = list(s.run(
            "SHOW CONSTRAINTS YIELD labelsOrTypes, properties "
            "RETURN labelsOrTypes, properties"
        ))
    pairs = {(tuple(c["labelsOrTypes"]), tuple(c["properties"])) for c in cons}
    assert (("Decision",), ("id",)) in pairs
    assert (("Contribution",),
            ("organization_id", "mapping_sha256", "source_sha256")) in pairs
