"""End-to-end integration test for the genomics-brca-mini bundle (#148).

Spins up Neo4j 5.15 via testcontainers. Runs resolve → plan → emit →
approve → submit. Asserts node/relationship counts and idempotency.
"""

import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner
from neo4j import GraphDatabase

from micromap_mapforge.cli import main

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]
BUNDLE_DIR = REPO_ROOT / "examples" / "genomics-brca-mini"

CSV_NAME = "brca_variants.csv"
ORG_ID = "genomics-brca-e2e-test"


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


def _invoke(runner: CliRunner, args: list[str]) -> None:
    result = runner.invoke(main, args)
    assert result.exit_code == 0, (
        f"command failed (exit={result.exit_code}):\n"
        f"  args:   mapforge {' '.join(args)}\n"
        f"  output: {result.output}"
    )


def _run_pipeline(runner, staged_bundle, creds, org_id):
    _invoke(runner, [
        "resolve", "--bundle", str(staged_bundle),
        "--neo4j-uri", creds["uri"],
        "--neo4j-user", creds["user"],
        "--neo4j-password", creds["password"],
    ])
    _invoke(runner, [
        "plan", "--bundle", str(staged_bundle),
        "--organization-id", org_id,
        "--policy", str(staged_bundle / "routing-policy.yaml"),
        "--contributor", str(staged_bundle / "contributor.yaml"),
    ])
    _invoke(runner, ["emit", "--bundle", str(staged_bundle)])
    _invoke(runner, [
        "approve", "--bundle", str(staged_bundle),
        "--reviewer", "e2e-test-reviewer",
    ])
    _invoke(runner, [
        "submit", "--bundle", str(staged_bundle),
        "--neo4j-uri", creds["uri"],
        "--neo4j-user", creds["user"],
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


def test_genomics_brca_end_to_end(staged_bundle, neo4j_credentials):
    """Full pipeline against a real Neo4j, asserting destination counts.

    CSV has ≥25 rows of BRCA1/BRCA2 pathogenic variants (2 genes).
    Each row → 1 Variant, 1 ASSOCIATED_WITH_DISEASE, 1 LOCATED_IN_GENE, 1 MENTIONED_IN.
    """
    runner = CliRunner()
    creds = neo4j_credentials
    _run_pipeline(runner, staged_bundle, creds, ORG_ID)

    driver = GraphDatabase.driver(
        creds["uri"], auth=(creds["user"], creds["password"])
    )
    try:
        with driver.session() as session:
            variant_count = _count_nodes(session, "Variant", ORG_ID)
            gene_count = _count_nodes(session, "Gene", ORG_ID)
            disease_count = _count_nodes(session, "Disease", ORG_ID)
            phenotype_count = _count_nodes(session, "Phenotype", ORG_ID)
            paper_count = _count_nodes(session, "Paper", ORG_ID)

            awd_count = _count_rels(session, "ASSOCIATED_WITH_DISEASE")
            lig_count = _count_rels(session, "LOCATED_IN_GENE")
            mi_count = _count_rels(session, "MENTIONED_IN")
    finally:
        driver.close()

    assert gene_count == 2, f"expected 2 Gene nodes (BRCA1 + BRCA2), got {gene_count}"
    assert variant_count >= 25, f"expected ≥25 Variant nodes, got {variant_count}"
    assert disease_count >= 1, f"expected ≥1 Disease node, got {disease_count}"
    assert phenotype_count >= 1, f"expected ≥1 Phenotype node, got {phenotype_count}"
    assert paper_count >= 1, f"expected ≥1 Paper node, got {paper_count}"
    assert awd_count == variant_count, (
        f"expected one ASSOCIATED_WITH_DISEASE per Variant ({variant_count}), got {awd_count}"
    )
    assert lig_count == variant_count, (
        f"expected one LOCATED_IN_GENE per Variant ({variant_count}), got {lig_count}"
    )
    assert mi_count == variant_count, (
        f"expected one MENTIONED_IN per Variant ({variant_count}), got {mi_count}"
    )


def test_genomics_brca_is_idempotent(staged_bundle, neo4j_credentials):
    """Re-running submit must not duplicate nodes."""
    runner = CliRunner()
    creds = neo4j_credentials
    _run_pipeline(runner, staged_bundle, creds, "genomics-brca-idempotent")

    driver = GraphDatabase.driver(
        creds["uri"], auth=(creds["user"], creds["password"])
    )
    try:
        with driver.session() as session:
            first_count = _count_nodes(session, "Variant", "genomics-brca-idempotent")
            first_rel = _count_rels(session, "LOCATED_IN_GENE")

        _invoke(runner, [
            "submit", "--bundle", str(staged_bundle),
            "--neo4j-uri", creds["uri"],
            "--neo4j-user", creds["user"],
            "--neo4j-password", creds["password"],
        ])

        with driver.session() as session:
            second_count = _count_nodes(session, "Variant", "genomics-brca-idempotent")
            second_rel = _count_rels(session, "LOCATED_IN_GENE")
    finally:
        driver.close()

    assert second_count == first_count, (
        f"Variant count drifted: {first_count} → {second_count}"
    )
    assert second_rel == first_rel, (
        f"LOCATED_IN_GENE count drifted: {first_rel} → {second_rel}"
    )
