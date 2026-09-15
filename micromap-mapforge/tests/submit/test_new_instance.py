import pytest
from pathlib import Path
from unittest.mock import MagicMock

import yaml

from micromap_mapforge.emit.bundle import IngestBundle
from micromap_mapforge.submit.new_instance import NewInstanceExecutor


def _bundle_with_routing(tmp_path: Path, routing: dict) -> IngestBundle:
    (tmp_path / "cypher").mkdir(exist_ok=True)
    (tmp_path / "routing.yaml").write_text(yaml.safe_dump(routing), encoding="utf-8")
    return IngestBundle(root=tmp_path)


FED_BLOCK = {
    "source_id": "acme-pharma",
    "display_name": "Acme",
    "base_url": "https://micromap.acme.example.com",
    "bolt_uri": "bolt+s://acme.example.com:7687",
    "bolt_auth_ref": "env:ACME_BOLT_PASSWORD",
    "capabilities": ["diseases.taxa"],
    "organization_id": "acme",
}


def _fake_driver():
    driver = MagicMock()
    session_ctx = MagicMock()
    session = MagicMock()
    driver.session.return_value = session_ctx
    session_ctx.__enter__ = MagicMock(return_value=session)
    session_ctx.__exit__ = MagicMock(return_value=False)

    def execute_write(fn):
        tx = MagicMock()
        result = MagicMock()
        result.consume.return_value = MagicMock(
            counters=MagicMock(nodes_created=1, relationships_created=0)
        )
        tx.run.return_value = result
        return fn(tx)

    session.execute_write = execute_write
    return driver


def test_read_federation_block_returns_block(tmp_path):
    bundle = _bundle_with_routing(tmp_path, {
        "destination": "new-federated-instance",
        "destinations": {"new-federated-instance": {"federation": FED_BLOCK}},
    })
    fed = NewInstanceExecutor()._read_federation_block(bundle)
    assert fed["source_id"] == "acme-pharma"


def test_read_federation_block_raises_when_routing_missing(tmp_path):
    (tmp_path / "cypher").mkdir()
    bundle = IngestBundle(root=tmp_path)
    with pytest.raises(ValueError, match="routing.yaml"):
        NewInstanceExecutor()._read_federation_block(bundle)


def test_read_federation_block_raises_when_destinations_missing(tmp_path):
    bundle = _bundle_with_routing(tmp_path, {"destination": "new-federated-instance"})
    with pytest.raises(ValueError, match="destinations.new-federated-instance.federation"):
        NewInstanceExecutor()._read_federation_block(bundle)


def test_dry_run_does_not_construct_driver(tmp_path, monkeypatch):
    monkeypatch.setenv("ACME_BOLT_PASSWORD", "shh")
    bundle = _bundle_with_routing(tmp_path, {
        "destination": "new-federated-instance",
        "destinations": {"new-federated-instance": {"federation": FED_BLOCK}},
    })
    (tmp_path / "cypher" / "nodes_Taxon.cypher").write_text(
        "MERGE (n:Taxon {ncbi_tax_id: '562'});\n", encoding="utf-8"
    )
    (tmp_path / "cypher" / "rels_X.cypher").write_text(
        "MERGE (a)-[:X]->(b);\n", encoding="utf-8"
    )

    def explode_driver(*a, **k):
        raise AssertionError("dry_run must not call driver_factory")

    report = NewInstanceExecutor(driver_factory=explode_driver).dry_run(bundle)
    assert report.destination == "new-federated-instance"
    assert report.would_write_nodes == 1
    assert report.would_write_relationships == 1
    assert "acme.example.com" in report.notes


def test_dry_run_raises_when_bolt_password_env_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("ACME_BOLT_PASSWORD", raising=False)
    bundle = _bundle_with_routing(tmp_path, {
        "destination": "new-federated-instance",
        "destinations": {"new-federated-instance": {"federation": FED_BLOCK}},
    })
    with pytest.raises(EnvironmentError, match="ACME_BOLT_PASSWORD"):
        NewInstanceExecutor().dry_run(bundle)


def test_submit_writes_via_bolt(tmp_path, monkeypatch):
    monkeypatch.setenv("ACME_BOLT_PASSWORD", "shh")
    (tmp_path / "cypher").mkdir()
    (tmp_path / "cypher" / "nodes_Taxon.cypher").write_text(
        "MERGE (n:Taxon {ncbi_tax_id: '562'});\n", encoding="utf-8"
    )
    bundle = _bundle_with_routing(tmp_path, {
        "destination": "new-federated-instance",
        "destinations": {"new-federated-instance": {"federation": FED_BLOCK}},
    })
    captured = {}

    def driver_factory(uri, *, auth):
        captured["uri"] = uri
        captured["auth"] = auth
        return _fake_driver()

    receipt = NewInstanceExecutor(driver_factory=driver_factory).submit(bundle)

    assert captured["uri"] == "bolt+s://acme.example.com:7687"
    assert captured["auth"] == ("mapforge", "shh")
    assert receipt.success is True
    assert receipt.destination == "new-federated-instance"
    assert receipt.nodes_written == 1


def test_submit_uses_default_bolt_user_when_omitted(tmp_path, monkeypatch):
    monkeypatch.setenv("ACME_BOLT_PASSWORD", "shh")
    (tmp_path / "cypher").mkdir()
    (tmp_path / "cypher" / "nodes_Taxon.cypher").write_text(
        "MERGE (n:Taxon {ncbi_tax_id: '562'});\n", encoding="utf-8"
    )
    bundle = _bundle_with_routing(tmp_path, {
        "destination": "new-federated-instance",
        "destinations": {"new-federated-instance": {"federation": FED_BLOCK}},
    })
    captured = {}

    def driver_factory(uri, *, auth):
        captured["auth"] = auth
        return _fake_driver()

    NewInstanceExecutor(driver_factory=driver_factory).submit(bundle)
    assert captured["auth"] == ("mapforge", "shh")


def test_submit_uses_overridden_bolt_user_from_federation_block(tmp_path, monkeypatch):
    monkeypatch.setenv("ACME_BOLT_PASSWORD", "shh")
    (tmp_path / "cypher").mkdir()
    (tmp_path / "cypher" / "nodes_Taxon.cypher").write_text(
        "MERGE (n:Taxon {ncbi_tax_id: '562'});\n", encoding="utf-8"
    )
    fed = dict(FED_BLOCK, bolt_user="neo4j")
    bundle = _bundle_with_routing(tmp_path, {
        "destination": "new-federated-instance",
        "destinations": {"new-federated-instance": {"federation": fed}},
    })
    captured = {}

    def driver_factory(uri, *, auth):
        captured["auth"] = auth
        return _fake_driver()

    NewInstanceExecutor(driver_factory=driver_factory).submit(bundle)
    assert captured["auth"] == ("neo4j", "shh")
