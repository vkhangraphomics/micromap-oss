"""Unit tests for the shared submit_bundle() core."""
import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from micromap_mapforge.submit.base import SubmissionReceipt
from micromap_mapforge.submit.service import (
    BundleManifestMissingError,
    BundleNotApprovedError,
    MissingReviewerError,
    MissingSourceShaError,
    resolve_source_sha,
    submit_bundle,
)


def _write_min_bundle(root: Path, *, approved: bool, org: str = "test-org") -> Path:
    (root / "cypher").mkdir(parents=True)
    (root / "cypher" / "nodes_Taxon.cypher").write_text(
        "UNWIND $batch_0 AS row "
        "MERGE (n:Taxon {ncbi_tax_id: row.ncbi_tax_id, organization_id: row.organization_id});\n",
        encoding="utf-8",
    )
    (root / "cypher" / "nodes_Taxon.params.json").write_text(
        json.dumps({"batch_0": [{"ncbi_tax_id": "562", "organization_id": org}]}),
        encoding="utf-8",
    )
    (root / "mapping.yaml").write_text(
        "source:\n  path: data.tsv\n  sha256: " + ("a" * 64) + "\n", encoding="utf-8"
    )
    (root / "routing.yaml").write_text(
        f"destination: micromap-core\norganization_id: {org}\n", encoding="utf-8"
    )
    (root / "resolution.json").write_text(
        json.dumps({"resolved_count": 1, "unresolved_count": 0, "ambiguous_count": 0,
                    "resolved": [], "unresolved": [], "ambiguous": []}),
        encoding="utf-8",
    )
    manifest = {"files": {}, "approved": approved}
    if approved:
        manifest["reviewer"] = "alice"
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_resolve_source_sha_prefers_sealed(tmp_path: Path):
    root = _write_min_bundle(tmp_path / "b", approved=True)
    mapping = {"source": {"path": "data.tsv", "sha256": "a" * 64}}
    assert resolve_source_sha(root, mapping) == "a" * 64


def test_missing_manifest_raises(tmp_path: Path):
    root = tmp_path / "b"
    (root / "cypher").mkdir(parents=True)
    (root / "routing.yaml").write_text("destination: micromap-core\norganization_id: x\n", encoding="utf-8")
    with pytest.raises(BundleManifestMissingError):
        submit_bundle(root, driver=object(), database="neo4j", reviewer="alice")


def test_unapproved_without_force_raises(tmp_path: Path):
    root = _write_min_bundle(tmp_path / "b", approved=False)
    with pytest.raises(BundleNotApprovedError):
        submit_bundle(root, driver=object(), database="neo4j", reviewer="alice")


def test_resolve_source_sha_falls_back_to_file(tmp_path: Path):
    content = b"col1\tcol2\nval1\tval2\n"
    (tmp_path / "data.tsv").write_bytes(content)
    result = resolve_source_sha(tmp_path, {"source": {"path": "data.tsv"}})
    assert result == hashlib.sha256(content).hexdigest()


def test_resolve_source_sha_raises_when_absent(tmp_path: Path):
    with pytest.raises(MissingSourceShaError):
        resolve_source_sha(tmp_path, {"source": {"path": "nope.tsv"}})


def _mock_driver() -> MagicMock:
    """Return a MagicMock Neo4j driver whose session.execute_write is a
    tracked MagicMock wrapping the real TX-function call.  Callers can
    inspect ``driver.session().__enter__().execute_write.call_count`` to
    assert whether the data write happened."""
    driver = MagicMock()
    session_ctx = MagicMock()
    session = MagicMock()
    driver.session.return_value = session_ctx
    session_ctx.__enter__ = MagicMock(return_value=session)
    session_ctx.__exit__ = MagicMock(return_value=False)

    def _execute_write_impl(fn):
        tx = MagicMock()
        result = MagicMock()
        result.consume.return_value = MagicMock(
            counters=MagicMock(nodes_created=1, relationships_created=0)
        )
        tx.run.return_value = result
        return fn(tx)

    # Wrap the implementation in a MagicMock so callers can assert on
    # call_count / assert_called / assert_not_called.
    session.execute_write = MagicMock(side_effect=_execute_write_impl)
    return driver


def test_force_bypasses_approval_gate(tmp_path: Path):
    root = _write_min_bundle(tmp_path / "b", approved=False)
    driver = _mock_driver()
    receipt = submit_bundle(root, driver=driver, database="neo4j", reviewer="alice", force=True)
    assert isinstance(receipt, SubmissionReceipt)


def test_build_contribution_record_fields(tmp_path: Path):
    from micromap_mapforge.submit.base import SubmissionReceipt
    from micromap_mapforge.submit.service import build_contribution_record

    root = _write_min_bundle(tmp_path / "b", approved=True, org="acme")
    routing = {"destination": "micromap-core", "organization_id": "acme"}
    receipt = SubmissionReceipt(destination="micromap-core", success=True,
                               nodes_written=1, relationships_written=0)
    rec = build_contribution_record(root, routing, "alice", receipt, force_submitted=None)
    assert rec.organization_id == "acme"
    assert rec.reviewer == "alice"
    assert rec.contributor == "acme"          # defaults to org when no provenance.contributor
    assert rec.source_sha256 == "a" * 64
    assert rec.destination == "micromap-core"
    assert rec.resolved_count == 1
    assert rec.force_submitted is None


def test_build_decision_record_fields(tmp_path: Path):
    from micromap_mapforge.submit.base import SubmissionReceipt
    from micromap_mapforge.submit.service import build_decision_record

    root = _write_min_bundle(tmp_path / "b", approved=True, org="acme")
    routing = {"destination": "micromap-core", "organization_id": "acme"}
    receipt = SubmissionReceipt(destination="micromap-core", success=True,
                               nodes_written=3, relationships_written=2)
    rec = build_decision_record(
        root, routing, receipt,
        about_tags=["crohn disease"], summary=None, rationale="because", actor="ci-agent",
    )
    assert rec.id.startswith("dec-contrib-")
    assert rec.organization_id == "acme"
    assert rec.actor_user == "ci-agent"
    assert rec.actor_org == "acme"
    assert rec.role == "service"
    assert rec.rationale == "because"
    assert rec.entity_tags == ["crohn disease"]
    assert rec.contribution_source_sha256 == "a" * 64
    assert rec.evidence_refs and rec.evidence_refs[0].startswith("mapforge:contribution/")
    assert rec.resolved_count == 1
    # summary defaults to the generated line when None
    assert "MapForge ingest" in rec.summary
    assert "3 nodes / 2 rels" in rec.summary


def test_derive_about_resolved_dedups_and_caps(tmp_path: Path):
    from micromap_mapforge.submit.service import derive_about_resolved
    root = tmp_path / "b"
    root.mkdir()
    (root / "resolution.json").write_text(__import__("json").dumps({
        "resolved": [
            {"entity_label": "Disease", "candidates": [
                {"merge_field": "doid", "merge_value": "DOID:1"}]},
            {"entity_label": "Disease", "candidates": [
                {"merge_field": "doid", "merge_value": "DOID:1"}]},   # dup
            {"entity_label": "Taxon", "candidates": [
                {"merge_field": "ncbi_tax_id", "merge_value": "562"}]},  # excluded label
        ]
    }), encoding="utf-8")
    out = derive_about_resolved(root)
    assert out == [{"label": "Disease", "field": "doid", "value": "DOID:1"}]


def test_reviewer_falls_back_to_manifest(tmp_path: Path):
    """Approved bundle with manifest reviewer; submit with reviewer=None should succeed."""
    root = _write_min_bundle(tmp_path / "b", approved=True, org="acme")
    # _write_min_bundle sets manifest["reviewer"] = "alice" when approved=True
    driver = _mock_driver()
    receipt = submit_bundle(root, driver=driver, database="neo4j", reviewer=None)
    assert receipt.success  # no MissingReviewerError — manifest reviewer "alice" was used


def test_missing_reviewer_raises_before_data_write(tmp_path: Path):
    """When neither arg nor manifest supplies a reviewer, MissingReviewerError must be
    raised BEFORE the core data write (fail-closed gate)."""
    import json as _json

    root = _write_min_bundle(tmp_path / "b", approved=True, org="acme")
    # Strip the reviewer from the manifest so no reviewer is available at all.
    m = _json.loads((root / "manifest.json").read_text())
    m.pop("reviewer", None)
    (root / "manifest.json").write_text(_json.dumps(m))

    drv = _mock_driver()
    with pytest.raises(MissingReviewerError):
        submit_bundle(root, driver=drv, database="neo4j", reviewer=None)

    # The data write must NOT have happened (fail-closed).
    # session.execute_write is the MagicMock we wrapped; it should be uncalled.
    session = drv.session().__enter__()
    session.execute_write.assert_not_called()


import pytest as _pytest


@_pytest.mark.integration
class TestSubmitBundleIntegration:
    @_pytest.fixture(scope="class")
    def neo4j_container(self):
        try:
            from testcontainers.neo4j import Neo4jContainer
        except ImportError:
            _pytest.skip("testcontainers[neo4j] not installed")
        try:
            with Neo4jContainer("neo4j:5.15") as n4j:
                yield n4j
        except Exception as e:
            _pytest.skip(f"Could not start Neo4j container: {e}")

    @_pytest.fixture
    def driver(self, neo4j_container):
        from neo4j import GraphDatabase
        drv = GraphDatabase.driver(
            neo4j_container.get_connection_url(),
            auth=(neo4j_container.username, neo4j_container.password),
        )
        yield drv
        drv.close()

    def test_round_trip_writes_data_and_contribution(self, driver, tmp_path):
        from micromap_mapforge.submit.service import submit_bundle
        root = _write_min_bundle(tmp_path / "b", approved=True, org="acme")
        receipt = submit_bundle(root, driver=driver, database="neo4j", reviewer="alice")
        assert receipt.success and receipt.nodes_written == 1
        with driver.session() as s:
            taxa = s.run("MATCH (n:Taxon {organization_id:'acme'}) RETURN count(n) AS c").single()["c"]
            contribs = s.run("MATCH (c:Contribution {organization_id:'acme'}) RETURN count(c) AS c").single()["c"]
        assert taxa == 1
        assert contribs == 1
