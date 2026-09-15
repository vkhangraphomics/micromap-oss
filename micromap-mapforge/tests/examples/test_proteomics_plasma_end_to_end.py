"""End-to-end integration test for the proteomics-plasma-mini bundle (#148)."""

import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner
from neo4j import GraphDatabase

from micromap_mapforge.cli import main

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]
BUNDLE_DIR = REPO_ROOT / "examples" / "proteomics-plasma-mini"

CSV_NAME = "plasma_proteins.csv"
ORG_ID = "proteomics-plasma-e2e-test"


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


def test_proteomics_plasma_end_to_end(staged_bundle, neo4j_credentials):
    """Full pipeline asserting node and relationship counts.

    CSV: 30 rows, 29 unique UniProt IDs (TP53/P04637 appears twice with
    different diseases), 29 unique gene symbols, >=12 diseases, >=10 pathways,
    1 tissue (blood plasma UBERON:0001969).

    Counts:
      Protein: 29 (TP53 deduplicated by uniprot_id=P04637)
      Gene: 29 (TP53 deduplicated by gene_symbol)
      Disease: >=12
      Pathway: >=10
      Tissue: 1
      ASSOCIATED_WITH_DISEASE: 30 (TP53 has 2 distinct disease rels)
      ENCODES: 29 (unique Gene->Protein pairs)
      PARTICIPATES_IN: 29 (unique Protein->Pathway; TP53 rows share hsa04115)
      SAMPLE_FROM_TISSUE: 29 (unique Protein->Tissue pairs)
    """
    runner = CliRunner()
    creds = neo4j_credentials
    _run_pipeline(runner, staged_bundle, creds, ORG_ID)

    driver = GraphDatabase.driver(creds["uri"], auth=(creds["user"], creds["password"]))
    try:
        with driver.session() as session:
            protein_count = _count_nodes(session, "Protein", ORG_ID)
            gene_count = _count_nodes(session, "Gene", ORG_ID)
            disease_count = _count_nodes(session, "Disease", ORG_ID)
            pathway_count = _count_nodes(session, "Pathway", ORG_ID)
            tissue_count = _count_nodes(session, "Tissue", ORG_ID)

            awd_count = _count_rels(session, "ASSOCIATED_WITH_DISEASE")
            enc_count = _count_rels(session, "ENCODES")
            pi_count = _count_rels(session, "PARTICIPATES_IN")
            sft_count = _count_rels(session, "SAMPLE_FROM_TISSUE")
    finally:
        driver.close()

    assert protein_count == 29, f"expected 29 Protein nodes, got {protein_count}"
    assert gene_count == 29, f"expected 29 Gene nodes, got {gene_count}"
    assert disease_count >= 12, f"expected >=12 Disease nodes, got {disease_count}"
    assert pathway_count >= 10, f"expected >=10 Pathway nodes, got {pathway_count}"
    assert tissue_count == 1, f"expected 1 Tissue node (blood plasma), got {tissue_count}"

    assert awd_count == 30, f"expected 30 ASSOCIATED_WITH_DISEASE, got {awd_count}"
    assert enc_count == 29, f"expected 29 ENCODES, got {enc_count}"
    assert pi_count == 29, f"expected 29 PARTICIPATES_IN, got {pi_count}"
    assert sft_count == 29, f"expected 29 SAMPLE_FROM_TISSUE, got {sft_count}"


def test_proteomics_plasma_is_idempotent(staged_bundle, neo4j_credentials):
    runner = CliRunner()
    creds = neo4j_credentials
    _run_pipeline(runner, staged_bundle, creds, "proteomics-idempotent")

    driver = GraphDatabase.driver(creds["uri"], auth=(creds["user"], creds["password"]))
    try:
        with driver.session() as session:
            first_protein = _count_nodes(session, "Protein", "proteomics-idempotent")
            first_awd = _count_rels(session, "ASSOCIATED_WITH_DISEASE")

        _invoke(runner, [
            "submit", "--bundle", str(staged_bundle),
            "--neo4j-uri", creds["uri"], "--neo4j-user", creds["user"],
            "--neo4j-password", creds["password"],
        ])

        with driver.session() as session:
            second_protein = _count_nodes(session, "Protein", "proteomics-idempotent")
            second_awd = _count_rels(session, "ASSOCIATED_WITH_DISEASE")
    finally:
        driver.close()

    assert second_protein == first_protein, (
        f"Protein count drifted: {first_protein} -> {second_protein}"
    )
    assert second_awd == first_awd, (
        f"ASSOCIATED_WITH_DISEASE count drifted: {first_awd} -> {second_awd}"
    )
