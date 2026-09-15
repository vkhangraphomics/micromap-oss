"""End-to-end integration: GTDB fixture → import-kg → submit → 206 nodes with provenance.

Mirrors the spike's proof (#80 README) but goes through the runner+serialize seam.
Requires Docker (testcontainers). Marked @pytest.mark.integration so unit CI skips it.

The fixture lives at ../mapforge-fixtures/gtdb_ar53_smoke.tsv (sibling of this repo;
referenced from PR #80). If unavailable, the test is skipped with a clear reason.
"""

import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from micromap_mapforge.cli import main


pytestmark = pytest.mark.integration


# This points at the sibling mapforge-fixtures repo; override via env.
DEFAULT_FIXTURE_PATH = Path(
    os.environ.get(
        "MAPFORGE_GTDB_FIXTURE",
        str(Path(__file__).resolve().parents[4] / "mapforge-fixtures" / "gtdb_ar53_smoke.tsv"),
    )
)


# The fake adapter is co-located in this test module so the test does not
# depend on a BioCypher install. The 206 number matches the spike's GTDB ar53
# slice (PR #80 README); we generate it deterministically from the fixture.
class GtdbAr53FixtureAdapter:
    name = "gtdb-ar53-smoke"
    schema_config = {
        "name": "biolink-mini",
        "prefixes": {"NCBITaxon": "http://purl.obolibrary.org/obo/NCBITaxon_"},
    }

    def __init__(self, fixture: Path):
        self._fixture = fixture

    def get_nodes(self):
        # The fixture TSV has 206 rows; each becomes one OrganismTaxon.
        for i, line in enumerate(self._fixture.read_text(encoding="utf-8").splitlines()):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # The fixture's column-0 is the GTDB accession; column-1 is the name.
            cols = line.split("\t")
            curie = f"NCBITaxon:{i+1}"  # synthetic but stable
            name = cols[1] if len(cols) > 1 else cols[0]
            yield (
                curie, "OrganismTaxon",
                {"name": name},
                {"source": "gtdb", "method": "curated", "ref": "ar53 smoke"},
                "EXTRACTED",
                None,
            )

    def get_edges(self):
        return iter(())


@pytest.fixture(scope="module")
def gtdb_fixture() -> Path:
    if not DEFAULT_FIXTURE_PATH.exists():
        pytest.skip(
            f"GTDB ar53 smoke fixture not found at {DEFAULT_FIXTURE_PATH}. "
            f"Clone mapforge-fixtures alongside graphomics-kg or set "
            f"MAPFORGE_GTDB_FIXTURE."
        )
    return DEFAULT_FIXTURE_PATH


@pytest.fixture(scope="module")
def neo4j_container():
    try:
        from testcontainers.neo4j import Neo4jContainer
    except ImportError:
        pytest.skip("testcontainers[neo4j] not installed")
    container = Neo4jContainer("neo4j:5.15")
    container.start()
    try:
        yield container
    finally:
        container.stop()


def test_import_kg_e2e_writes_206_organism_taxon_nodes_with_provenance(
    tmp_path: Path,
    gtdb_fixture: Path,
    neo4j_container,
    monkeypatch,
):
    # Patch the adapter dotted path target so it can construct from the fixture path.
    monkeypatch.setattr(
        "tests.integration_biocypher.test_import_kg_e2e.GtdbAr53FixtureAdapter",
        lambda: GtdbAr53FixtureAdapter(gtdb_fixture),
        raising=True,
    )

    out_dir = tmp_path / "bundle"
    runner = CliRunner()
    result = runner.invoke(main, [
        "import-kg",
        "tests.integration_biocypher.test_import_kg_e2e:GtdbAr53FixtureAdapter",
        "--source", str(gtdb_fixture),
        "--organization-id", "org-gtdb-e2e",
        "--out", str(out_dir),
    ])
    assert result.exit_code == 0, result.output

    # Approve so submit will run.
    result = runner.invoke(main, ["approve", "--bundle", str(out_dir), "--reviewer", "tester"])
    assert result.exit_code == 0, result.output

    uri = neo4j_container.get_connection_url()  # bolt://host:port
    user = "neo4j"
    # testcontainers 4.x exposes the container's password via container.password
    # (or NEO4J_AUTH=neo4j/<pw> on the container env). The hasattr/'test'
    # fallback used previously was wrong on every released version — the
    # container ships with neo4j/'password' by default, so auth would fail
    # the moment this test ran for real. Probe both attributes and fall back
    # to the documented default. Review finding #4.
    password = (
        getattr(neo4j_container, "password", None)
        or getattr(neo4j_container, "NEO4J_ADMIN_PASSWORD", None)
        or "password"
    )

    result = runner.invoke(main, [
        "submit",
        "--bundle", str(out_dir),
        "--reviewer", "tester",
        "--neo4j-uri", uri,
        "--neo4j-user", user,
        "--neo4j-password", password,
        "--neo4j-database", "neo4j",
    ])
    assert result.exit_code == 0, result.output

    # Assert directly against Neo4j: 206 OrganismTaxon nodes with provenance.
    from neo4j import GraphDatabase
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session(database="neo4j") as s:
            count = s.run(
                "MATCH (n:OrganismTaxon {organization_id: $org}) RETURN count(n) AS c",
                org="org-gtdb-e2e",
            ).single()["c"]
            assert count == 206

            sample = s.run(
                "MATCH (n:OrganismTaxon {organization_id: $org}) "
                "RETURN n.provenance_source AS src, n.confidence AS conf LIMIT 1",
                org="org-gtdb-e2e",
            ).single()
            assert sample["src"] == "gtdb"
            assert sample["conf"] == "EXTRACTED"
    finally:
        driver.close()
