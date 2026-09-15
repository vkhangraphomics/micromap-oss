"""End-to-end integration test for the metabolomics-serum-mini bundle (#148).

CRITICAL: metabolomics template uses BIOMARKER_FOR (not LINKED_TO_DISEASE).
"""

import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner
from neo4j import GraphDatabase

from micromap_mapforge.cli import main

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]
BUNDLE_DIR = REPO_ROOT / "examples" / "metabolomics-serum-mini"

CSV_NAME = "serum_metabolites.csv"
ORG_ID = "metabolomics-serum-e2e-test"


@pytest.fixture(scope="module")
def neo4j_container():
    try:
        from testcontainers.neo4j import Neo4jContainer
    except ImportError:
        pytest.skip("testcontainers[neo4j] not installed")
    try:
        with Neo4jContainer("neo4j:5.15") as n4j:
            yield n4j
    except Exception as exc:
        pytest.skip(f"Could not start Neo4j container: {exc}")


@pytest.fixture
def neo4j_credentials(neo4j_container):
    return {
        "uri": neo4j_container.get_connection_url(),
        "user": neo4j_container.username,
        "password": neo4j_container.password,
    }


@pytest.fixture
def staged_bundle(tmp_path: Path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for name in ("mapping.yaml", "routing-policy.yaml", "contributor.yaml", CSV_NAME):
        shutil.copy(BUNDLE_DIR / name, bundle / name)
    return bundle


def _invoke(runner, args):
    result = runner.invoke(main, args)
    assert result.exit_code == 0, (
        f"command failed (exit={result.exit_code}):\n"
        f"  args:   mapforge {' '.join(args)}\n"
        f"  output: {result.output}"
    )


def _run_pipeline(runner, staged_bundle, creds, org_id):
    _invoke(runner, [
        "resolve", "--bundle", str(staged_bundle),
        "--neo4j-uri", creds["uri"], "--neo4j-user", creds["user"],
        "--neo4j-password", creds["password"],
    ])
    _invoke(runner, [
        "plan", "--bundle", str(staged_bundle),
        "--organization-id", org_id,
        "--policy", str(staged_bundle / "routing-policy.yaml"),
        "--contributor", str(staged_bundle / "contributor.yaml"),
    ])
    _invoke(runner, ["emit", "--bundle", str(staged_bundle)])
    _invoke(runner, ["approve", "--bundle", str(staged_bundle), "--reviewer", "e2e-reviewer"])
    _invoke(runner, [
        "submit", "--bundle", str(staged_bundle),
        "--neo4j-uri", creds["uri"], "--neo4j-user", creds["user"],
        "--neo4j-password", creds["password"],
    ])


def _count_nodes(session, label, org_id):
    return session.run(
        f"MATCH (n:{label} {{organization_id: $org}}) RETURN count(n) AS c",
        org=org_id,
    ).single()["c"]


def _count_rels(session, rel_type):
    return session.run(
        f"MATCH ()-[r:{rel_type}]->() RETURN count(r) AS c"
    ).single()["c"]


def test_metabolomics_serum_end_to_end(staged_bundle, neo4j_credentials):
    """Full pipeline asserting node and relationship counts.

    CSV: 30 rows, 30 unique HMDB IDs (Compounds), >=13 unique Diseases,
    >=10 unique Pathways, 1 BodySite (serum UBERON:0001977).
    Relationships: 30 BIOMARKER_FOR, 30 PARTICIPATES_IN, 30 FOUND_IN.
    """
    runner = CliRunner()
    creds = neo4j_credentials
    _run_pipeline(runner, staged_bundle, creds, ORG_ID)

    driver = GraphDatabase.driver(creds["uri"], auth=(creds["user"], creds["password"]))
    try:
        with driver.session() as session:
            compound_count = _count_nodes(session, "Compound", ORG_ID)
            disease_count = _count_nodes(session, "Disease", ORG_ID)
            pathway_count = _count_nodes(session, "Pathway", ORG_ID)
            bodysite_count = _count_nodes(session, "BodySite", ORG_ID)

            bf_count = _count_rels(session, "BIOMARKER_FOR")
            pi_count = _count_rels(session, "PARTICIPATES_IN")
            fi_count = _count_rels(session, "FOUND_IN")
    finally:
        driver.close()

    assert compound_count == 30, f"expected 30 Compound nodes, got {compound_count}"
    assert disease_count >= 13, f"expected >=13 Disease nodes, got {disease_count}"
    assert pathway_count >= 10, f"expected >=10 Pathway nodes, got {pathway_count}"
    assert bodysite_count == 1, f"expected 1 BodySite node (serum), got {bodysite_count}"

    assert bf_count == 30, f"expected 30 BIOMARKER_FOR, got {bf_count}"
    assert pi_count == 30, f"expected 30 PARTICIPATES_IN, got {pi_count}"
    assert fi_count == 30, f"expected 30 FOUND_IN, got {fi_count}"


def test_metabolomics_serum_is_idempotent(staged_bundle, neo4j_credentials):
    runner = CliRunner()
    creds = neo4j_credentials
    _run_pipeline(runner, staged_bundle, creds, "metabolomics-idempotent")

    driver = GraphDatabase.driver(creds["uri"], auth=(creds["user"], creds["password"]))
    try:
        with driver.session() as session:
            first_compound = _count_nodes(session, "Compound", "metabolomics-idempotent")
            first_bf = _count_rels(session, "BIOMARKER_FOR")

        _invoke(runner, [
            "submit", "--bundle", str(staged_bundle),
            "--neo4j-uri", creds["uri"], "--neo4j-user", creds["user"],
            "--neo4j-password", creds["password"],
        ])

        with driver.session() as session:
            second_compound = _count_nodes(session, "Compound", "metabolomics-idempotent")
            second_bf = _count_rels(session, "BIOMARKER_FOR")
    finally:
        driver.close()

    assert second_compound == first_compound, (
        f"Compound count drifted: {first_compound} -> {second_compound}"
    )
    assert second_bf == first_bf, (
        f"BIOMARKER_FOR count drifted: {first_bf} -> {second_bf}"
    )
