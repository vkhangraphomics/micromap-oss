"""End-to-end integration test for the transcriptomics-alzheimer-mini bundle (#148)."""

import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner
from neo4j import GraphDatabase

from micromap_mapforge.cli import main

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]
BUNDLE_DIR = REPO_ROOT / "examples" / "transcriptomics-alzheimer-mini"

CSV_NAME = "alzheimer_degs.csv"
ORG_ID = "alzheimer-e2e-test"


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


def test_transcriptomics_alzheimer_end_to_end(staged_bundle, neo4j_credentials):
    """Full pipeline asserting node and relationship counts.

    CSV has 30 rows: 6 unique genes, 9 unique samples, 6 unique tissues, 1 condition.
    DEI is Gene→Condition: 30 rows collapse to 6 unique (gene, AD) pairs.
    Relationships: 6 DIFFERENTIALLY_EXPRESSED_IN, 9 SAMPLE_FROM_TISSUE,
    9 SAMPLE_HAS_CONDITION.
    """
    runner = CliRunner()
    creds = neo4j_credentials
    _run_pipeline(runner, staged_bundle, creds, ORG_ID)

    driver = GraphDatabase.driver(creds["uri"], auth=(creds["user"], creds["password"]))
    try:
        with driver.session() as session:
            gene_count = _count_nodes(session, "Gene", ORG_ID)
            sample_count = _count_nodes(session, "Sample", ORG_ID)
            tissue_count = _count_nodes(session, "Tissue", ORG_ID)
            condition_count = _count_nodes(session, "Condition", ORG_ID)

            dei_count = _count_rels(session, "DIFFERENTIALLY_EXPRESSED_IN")
            sft_count = _count_rels(session, "SAMPLE_FROM_TISSUE")
            shc_count = _count_rels(session, "SAMPLE_HAS_CONDITION")
    finally:
        driver.close()

    assert gene_count == 6, f"expected 6 Gene nodes, got {gene_count}"
    assert sample_count == 9, f"expected 9 Sample nodes, got {sample_count}"
    assert tissue_count == 6, f"expected 6 Tissue nodes, got {tissue_count}"
    assert condition_count == 1, f"expected 1 Condition node (AD), got {condition_count}"
    assert dei_count == 6, f"expected 6 DIFFERENTIALLY_EXPRESSED_IN (6 genes × 1 AD condition), got {dei_count}"
    assert sft_count == 9, f"expected 9 SAMPLE_FROM_TISSUE, got {sft_count}"
    assert shc_count == 9, f"expected 9 SAMPLE_HAS_CONDITION, got {shc_count}"


def test_transcriptomics_alzheimer_is_idempotent(staged_bundle, neo4j_credentials):
    runner = CliRunner()
    creds = neo4j_credentials
    _run_pipeline(runner, staged_bundle, creds, "alzheimer-idempotent")

    driver = GraphDatabase.driver(creds["uri"], auth=(creds["user"], creds["password"]))
    try:
        with driver.session() as session:
            first_gene = _count_nodes(session, "Gene", "alzheimer-idempotent")
            first_dei = _count_rels(session, "DIFFERENTIALLY_EXPRESSED_IN")

        _invoke(runner, [
            "submit", "--bundle", str(staged_bundle),
            "--neo4j-uri", creds["uri"], "--neo4j-user", creds["user"],
            "--neo4j-password", creds["password"],
        ])

        with driver.session() as session:
            second_gene = _count_nodes(session, "Gene", "alzheimer-idempotent")
            second_dei = _count_rels(session, "DIFFERENTIALLY_EXPRESSED_IN")
    finally:
        driver.close()

    assert second_gene == first_gene, f"Gene count drifted: {first_gene} → {second_gene}"
    assert second_dei == first_dei, f"DEI count drifted: {first_dei} → {second_dei}"
