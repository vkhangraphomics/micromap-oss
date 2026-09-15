"""End-to-end integration test for the shipped Disbiome bundle (#66 DoD).

Spins up a Neo4j 5.x container via testcontainers and runs the full
`inspect → map → resolve → plan → emit → approve → submit` flow against
the shipped `examples/disbiome/` bundle. Asserts the resulting node and
relationship counts match what the validation pass produced (#65).

This is the "reproducible end-to-end example bundle, runnable in CI and on
a dev machine in <5 min" deliverable from #66. The test is marked
`@pytest.mark.integration` so it lands in the existing `mapforge-integration`
CI job (where testcontainers is set up); the unit-test job skips it.

The DoD says **example bundle in tests/golden/ (or equivalent)** — we use
the *production* shipped example at `examples/disbiome/` rather than a
test-tree fixture, so this test also acts as a regression guard on the
shipped walkthrough: if any commit silently breaks `examples/disbiome/`,
this test catches it.
"""

import shutil
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from neo4j import GraphDatabase

from micromap_mapforge.cli import main


pytestmark = pytest.mark.integration


# Repo root: tests/examples/<this file> → micromap-mapforge → repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
DISBIOME_DIR = REPO_ROOT / "examples" / "disbiome"


@pytest.fixture(scope="module")
def neo4j_container():
    try:
        from testcontainers.neo4j import Neo4jContainer
    except ImportError:
        pytest.skip("testcontainers[neo4j] not installed")
    try:
        # Neo4j 5.15 matches the pin documented in README's compat matrix
        # and the other integration tests in this suite.
        with Neo4jContainer("neo4j:5.15") as n4j:
            yield n4j
    except Exception as exc:
        pytest.skip(f"Could not start Neo4j container: {exc}")


@pytest.fixture
def neo4j_credentials(neo4j_container):
    """Bolt URI + auth for the running container."""
    return {
        "uri": neo4j_container.get_connection_url(),
        "user": neo4j_container.username,
        "password": neo4j_container.password,
    }


@pytest.fixture
def staged_bundle(tmp_path: Path):
    """Copy the shipped Disbiome example into a tmp bundle dir and rewrite
    `source.path` to be absolute, since the shipped mapping references the
    CSV by relative path (`disbiome_sample.csv`).

    Using a tmp_path means this test never mutates the shipped example dir,
    even on assertion failure.
    """
    bundle = tmp_path / "bundle"
    bundle.mkdir()

    # Copy the four shipped artifacts + the data file.
    for name in (
        "mapping.yaml",
        "routing-policy.yaml",
        "contributor.yaml",
        "disbiome_sample.csv",
    ):
        shutil.copy(DISBIOME_DIR / name, bundle / name)

    # Rewrite the mapping's source.path so it resolves regardless of CWD.
    # (#116's fix anchors relative paths to the bundle dir; this stays
    # bundle-relative — i.e., 'disbiome_sample.csv' — which now works.)
    return bundle


def _invoke(runner: CliRunner, args: list[str]) -> None:
    """Run a CLI command and fail the test on non-zero exit with the output."""
    result = runner.invoke(main, args)
    assert result.exit_code == 0, (
        f"command failed (exit={result.exit_code}):\n"
        f"  args:   mapforge {' '.join(args)}\n"
        f"  output: {result.output}"
    )


def test_disbiome_end_to_end(staged_bundle, neo4j_credentials):
    """Full pipeline against a real Neo4j, asserting destination counts.

    Stage list (matches the contributor flow doc):
      1. inspect      — already done by the staged bundle; skip
      2. map          — already done (shipped mapping.yaml); skip
      3. resolve      — empty Neo4j → all entities unresolved (expected)
      4. plan         — apply routing-policy → micromap-core
      5. emit         — write cypher/ + INGEST_REPORT.md + manifest.json
      6. approve      — record reviewer
      7. submit       — execute Cypher against Neo4j, write Contribution

    The shipped Disbiome bundle has 50 source rows producing 106 unique
    entities (37 Taxa + 22 Diseases + 47 Papers) and 100 relationships
    (50 ASSOCIATED_WITH_DISEASE + 50 MENTIONED_IN). These counts match
    what the #65 validation pass produced.
    """
    runner = CliRunner()
    creds = neo4j_credentials

    # Stage 3: resolve
    _invoke(runner, [
        "resolve",
        "--bundle", str(staged_bundle),
        "--neo4j-uri", creds["uri"],
        "--neo4j-user", creds["user"],
        "--neo4j-password", creds["password"],
    ])
    assert (staged_bundle / "resolution.json").exists()

    # Stage 4: plan (provides organization_id + routing decision)
    _invoke(runner, [
        "plan",
        "--bundle", str(staged_bundle),
        "--organization-id", "disbiome-e2e-test",
        "--policy", str(staged_bundle / "routing-policy.yaml"),
        "--contributor", str(staged_bundle / "contributor.yaml"),
    ])
    routing = yaml.safe_load((staged_bundle / "routing.yaml").read_text(encoding="utf-8"))
    assert routing["destination"] == "micromap-core"
    assert routing["organization_id"] == "disbiome-e2e-test"

    # Stage 5: emit
    _invoke(runner, ["emit", "--bundle", str(staged_bundle)])
    assert (staged_bundle / "INGEST_REPORT.md").exists()
    assert (staged_bundle / "manifest.json").exists()
    cypher_dir = staged_bundle / "cypher"
    assert (cypher_dir / "nodes_Taxon.cypher").exists()
    assert (cypher_dir / "nodes_Disease.cypher").exists()
    assert (cypher_dir / "nodes_Paper.cypher").exists()
    assert (cypher_dir / "rels_ASSOCIATED_WITH_DISEASE.cypher").exists()
    assert (cypher_dir / "rels_MENTIONED_IN.cypher").exists()

    # Stage 6: approve
    _invoke(runner, [
        "approve",
        "--bundle", str(staged_bundle),
        "--reviewer", "e2e-test-reviewer",
    ])

    # Stage 7: submit (writes data + provenance)
    _invoke(runner, [
        "submit",
        "--bundle", str(staged_bundle),
        "--neo4j-uri", creds["uri"],
        "--neo4j-user", creds["user"],
        "--neo4j-password", creds["password"],
    ])

    # Verify the destination Neo4j matches expected counts.
    driver = GraphDatabase.driver(
        creds["uri"], auth=(creds["user"], creds["password"])
    )
    try:
        with driver.session() as session:
            counts = {}
            for label in ("Taxon", "Disease", "Paper",
                          "Organization", "Reviewer", "Contribution"):
                rec = session.run(
                    f"MATCH (n:{label} {{organization_id: $org}}) RETURN count(n) AS c"
                    if label not in ("Organization", "Reviewer")
                    else f"MATCH (n:{label}) RETURN count(n) AS c",
                    org="disbiome-e2e-test",
                ).single()
                counts[label] = rec["c"]
            rel_counts = {}
            for rel in ("ASSOCIATED_WITH_DISEASE", "MENTIONED_IN",
                        "CONTRIBUTED", "APPROVED_BY"):
                rec = session.run(
                    f"MATCH ()-[r:{rel}]->() RETURN count(r) AS c"
                ).single()
                rel_counts[rel] = rec["c"]
    finally:
        driver.close()

    # Data nodes — match #65 validation-pass counts.
    assert counts["Taxon"] == 37, f"expected 37 Taxon nodes, got {counts['Taxon']}"
    assert counts["Disease"] == 22, f"expected 22 Disease nodes, got {counts['Disease']}"
    assert counts["Paper"] == 47, f"expected 47 Paper nodes, got {counts['Paper']}"

    # Data relationships — 50 rows × 2 rel types.
    assert rel_counts["ASSOCIATED_WITH_DISEASE"] == 50
    assert rel_counts["MENTIONED_IN"] == 50

    # Provenance triple — one of each per submission.
    assert counts["Organization"] == 1
    assert counts["Reviewer"] == 1
    assert counts["Contribution"] == 1
    assert rel_counts["CONTRIBUTED"] == 1
    assert rel_counts["APPROVED_BY"] == 1


def test_disbiome_end_to_end_is_idempotent(staged_bundle, neo4j_credentials):
    """Re-running submit on the same bundle must not duplicate nodes.

    Submit uses MERGE on `(natural_key, organization_id)`; running it twice
    against the same Neo4j with the same organization_id should produce the
    same node/rel counts as a single run. This is the property that lets
    operators safely re-run a failed submit.
    """
    runner = CliRunner()
    creds = neo4j_credentials

    # Build the bundle once.
    args_resolve = [
        "resolve", "--bundle", str(staged_bundle),
        "--neo4j-uri", creds["uri"], "--neo4j-user", creds["user"],
        "--neo4j-password", creds["password"],
    ]
    args_plan = [
        "plan", "--bundle", str(staged_bundle),
        "--organization-id", "disbiome-idempotent-test",
        "--policy", str(staged_bundle / "routing-policy.yaml"),
        "--contributor", str(staged_bundle / "contributor.yaml"),
    ]
    args_emit = ["emit", "--bundle", str(staged_bundle)]
    args_approve = ["approve", "--bundle", str(staged_bundle),
                    "--reviewer", "idempotent-reviewer"]
    args_submit = [
        "submit", "--bundle", str(staged_bundle),
        "--neo4j-uri", creds["uri"], "--neo4j-user", creds["user"],
        "--neo4j-password", creds["password"],
    ]
    for args in (args_resolve, args_plan, args_emit, args_approve, args_submit):
        _invoke(runner, args)

    driver = GraphDatabase.driver(
        creds["uri"], auth=(creds["user"], creds["password"])
    )
    try:
        with driver.session() as session:
            first_taxon_count = session.run(
                "MATCH (n:Taxon {organization_id: 'disbiome-idempotent-test'}) "
                "RETURN count(n) AS c"
            ).single()["c"]
            first_rel_count = session.run(
                "MATCH ()-[r:ASSOCIATED_WITH_DISEASE]->() "
                "WHERE r.organization_id = 'disbiome-idempotent-test' "
                "RETURN count(r) AS c"
            ).single()["c"]

        # Second submit — same bundle, same org_id.
        _invoke(runner, args_submit)

        with driver.session() as session:
            second_taxon_count = session.run(
                "MATCH (n:Taxon {organization_id: 'disbiome-idempotent-test'}) "
                "RETURN count(n) AS c"
            ).single()["c"]
            second_rel_count = session.run(
                "MATCH ()-[r:ASSOCIATED_WITH_DISEASE]->() "
                "WHERE r.organization_id = 'disbiome-idempotent-test' "
                "RETURN count(r) AS c"
            ).single()["c"]
    finally:
        driver.close()

    assert second_taxon_count == first_taxon_count, (
        f"Taxon count drifted between submits: {first_taxon_count} → {second_taxon_count}"
    )
    assert second_rel_count == first_rel_count, (
        f"Relationship count drifted between submits: "
        f"{first_rel_count} → {second_rel_count}"
    )
