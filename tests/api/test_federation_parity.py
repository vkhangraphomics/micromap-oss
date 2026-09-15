"""Parity: CLI submit (Bolt) vs HTTP route produce identical graph + provenance."""
import io
import json
import tarfile
from pathlib import Path

import pytest

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


def _make_bundle(root: Path, org: str) -> Path:
    (root / "cypher").mkdir(parents=True)
    (root / "cypher" / "nodes_Taxon.cypher").write_text(
        "UNWIND $batch_0 AS row MERGE (n:Taxon {ncbi_tax_id: row.ncbi_tax_id, "
        "organization_id: row.organization_id}) SET n.name = row.name;\n", encoding="utf-8")
    (root / "cypher" / "nodes_Taxon.params.json").write_text(json.dumps(
        {"batch_0": [{"ncbi_tax_id": "562", "organization_id": org, "name": "E. coli"}]}),
        encoding="utf-8")
    (root / "mapping.yaml").write_text(
        "source:\n  path: d.tsv\n  sha256: " + "b" * 64 + "\n", encoding="utf-8")
    (root / "routing.yaml").write_text(
        f"destination: micromap-core\norganization_id: {org}\n", encoding="utf-8")
    (root / "resolution.json").write_text(json.dumps(
        {"resolved_count": 1, "unresolved_count": 0, "ambiguous_count": 0, "resolved": []}),
        encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps(
        {"files": {}, "approved": True, "reviewer": "alice"}), encoding="utf-8")
    return root


def _counts(driver, org):
    with driver.session() as s:
        taxa = s.run("MATCH (n:Taxon {organization_id:$o}) RETURN count(n) AS c", o=org).single()["c"]
        contrib = s.run("MATCH (c:Contribution {organization_id:$o}) RETURN count(c) AS c", o=org).single()["c"]
    return taxa, contrib


def test_http_matches_bolt(neo4j_container, tmp_path):
    from unittest.mock import patch
    from neo4j import GraphDatabase
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from micromap_mapforge.submit.service import submit_bundle
    from api.federation_tenants import FederationTarget
    from api.routes import federation

    url = neo4j_container.get_connection_url()
    auth = (neo4j_container.username, neo4j_container.password)
    driver = GraphDatabase.driver(url, auth=auth)

    # --- Bolt path (CLI core) for org A ---
    # Use reviewer=None to prove the manifest reviewer ("alice") is picked up automatically.
    bolt_bundle = _make_bundle(tmp_path / "bolt", "orgA")
    submit_bundle(bolt_bundle, driver=driver, database="neo4j", reviewer=None)
    bolt_taxa, bolt_contrib = _counts(driver, "orgA")

    # --- HTTP path for org B ---
    # No "reviewer" form field — the route relies on the manifest reviewer too.
    http_bundle = _make_bundle(tmp_path / "http", "orgB")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for p in sorted(http_bundle.rglob("*")):
            if p.is_file():
                tar.add(p, arcname=p.relative_to(http_bundle).as_posix())
    target = FederationTarget(organization_id="orgB", database="neo4j",
                              bolt_uri=url, bolt_auth_ref="env:UNUSED")
    app = FastAPI(); app.include_router(federation.router, prefix="/api/v1")
    client = TestClient(app, raise_server_exceptions=False)
    with patch("api.routes.federation.resolve_federation_target", return_value=target), \
         patch("api.routes.federation.resolve_bolt_password", return_value=auth[1]), \
         patch("api.routes.federation.GraphDatabase") as gdb:
        gdb.driver.return_value = GraphDatabase.driver(url, auth=auth)
        r = client.post("/api/v1/federation/contributions",
                        headers={"X-API-Key": "key_b"},
                        files={"bundle": ("b.tgz", buf.getvalue(), "application/gzip")})
    assert r.status_code == 200, r.text
    http_taxa, http_contrib = _counts(driver, "orgB")
    driver.close()

    assert (http_taxa, http_contrib) == (bolt_taxa, bolt_contrib) == (1, 1)
