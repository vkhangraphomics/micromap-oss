"""Failure modes the `submit` step has to survive cleanly (issue #62 scope).

These tests do not require a live Neo4j — they exercise the CLI's
pre-flight checks and its handling of executor-side failures using mocks.
The "submit + roll back across multiple batches against a real database"
contract belongs in an integration-marked test, tracked as future work.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from click.testing import CliRunner

from micromap_mapforge.cli import main


def _seed_emitted_bundle(tmp_path: Path) -> Path:
    """Minimal bundle that's already been through emit (has manifest.json).

    Mirrors test_cli_emit_submit.py's helper but inlined here so the failure
    suite doesn't take a dependency on that file's internals.
    """
    out = tmp_path / "out"
    out.mkdir(parents=True)
    (out / "mapping.yaml").write_text(yaml.safe_dump({
        "source": {"name": "s", "format": "tsv", "path": str(tmp_path / "s.tsv")},
        "entities": [
            {"label": "Taxon", "match_on": "ncbi_tax_id",
             "columns": {"ncbi_tax_id": "tax_id"}, "confidence": "EXTRACTED"},
        ],
        "relationships": [],
    }), encoding="utf-8")
    (out / "routing.yaml").write_text(yaml.safe_dump({
        "destination": "micromap-core",
        "organization_id": "org-xyz",
        "provenance": {"contributor": "org-xyz", "submitted_at": "now",
                       "mapping_version": "sha256:abc"},
        "federation_endpoint": {"type": None, "uri": None},
    }), encoding="utf-8")
    (out / "resolution.json").write_text(json.dumps({
        "resolved": [{"entity_label": "Taxon", "source_term": "562", "candidates": [
            {"node_id": "ncbi_tax_id:562", "match_type": "EXTRACTED", "score": 1.0,
             "reason": "", "merge_field": "ncbi_tax_id", "merge_value": "562"}
        ]}],
        "unresolved": [], "ambiguous": [],
        "resolved_count": 1, "unresolved_count": 0, "ambiguous_count": 0,
    }), encoding="utf-8")
    (tmp_path / "s.tsv").write_text("tax_id\torganism\n562\tEscherichia coli\n",
                                    encoding="utf-8")

    runner = CliRunner()
    runner.invoke(main, ["emit", "--bundle", str(out)])
    # Approve so submit doesn't bail on the approval check.
    runner.invoke(main, ["approve", "--bundle", str(out), "--reviewer", "test"])
    return out


def test_submit_without_neo4j_credentials_fails_fast(tmp_path):
    """Production scenario: an operator forgot to pass --neo4j-* flags.
    Must exit non-zero BEFORE constructing any executor or driver."""
    bundle_dir = _seed_emitted_bundle(tmp_path)

    runner = CliRunner()
    with patch("micromap_mapforge.cli._executor_for_bundle") as factory:
        result = runner.invoke(
            main,
            ["submit", "--bundle", str(bundle_dir), "--reviewer", "tester"],
        )
    assert result.exit_code != 0
    assert "--neo4j-uri" in result.output or "neo4j" in result.output.lower()
    # The executor must NOT have been built — that's the "fail fast, no
    # partial writes" contract.
    assert not factory.called


def test_submit_propagates_executor_failure_with_clean_close(tmp_path):
    """Production scenario: Neo4j drops mid-submit. The CLI must:
    - close the driver (no leaked sessions),
    - exit non-zero,
    - NOT try to write a Contribution node (a successful Contribution
      record for a failed submission would lie in the audit log)."""
    bundle_dir = _seed_emitted_bundle(tmp_path)

    fake_driver = MagicMock(name="fake-driver")
    fake_executor = MagicMock()
    fake_executor.submit.side_effect = RuntimeError("Neo4j connection dropped")

    runner = CliRunner()
    with patch("micromap_mapforge.cli._executor_for_bundle",
               return_value=(fake_executor, fake_driver)), \
         patch("micromap_mapforge.cli._write_contribution_for_bundle") as contrib:
        result = runner.invoke(
            main,
            ["submit", "--bundle", str(bundle_dir), "--reviewer", "alice",
             "--neo4j-uri", "bolt://localhost", "--neo4j-user", "u",
             "--neo4j-password", "p"],
        )
    assert result.exit_code != 0
    fake_driver.close.assert_called_once()
    # Contribution node must NOT be written for a failed submission.
    assert not contrib.called


def test_submit_reports_non_success_receipt_without_contribution(tmp_path):
    """Production scenario: the executor returned but its receipt says
    success=False (constraint violation rolled back the batch, etc.).
    Must exit non-zero and skip the Contribution write."""
    bundle_dir = _seed_emitted_bundle(tmp_path)

    fake_receipt = MagicMock(success=False, destination="micromap-core",
                             nodes_written=0, relationships_written=0,
                             notes="constraint violation on Taxon.ncbi_tax_id")
    fake_executor = MagicMock()
    fake_executor.submit.return_value = fake_receipt

    runner = CliRunner()
    with patch("micromap_mapforge.cli._executor_for_bundle",
               return_value=(fake_executor, MagicMock())), \
         patch("micromap_mapforge.cli._write_contribution_for_bundle") as contrib:
        result = runner.invoke(
            main,
            ["submit", "--bundle", str(bundle_dir), "--reviewer", "alice",
             "--neo4j-uri", "bolt://localhost", "--neo4j-user", "u",
             "--neo4j-password", "p"],
        )
    assert result.exit_code != 0
    assert not contrib.called
    # The receipt's notes should make it into the CLI output so an
    # operator knows why the submission failed.
    assert "constraint" in result.output.lower()


def test_submit_without_manifest_fails_before_any_write(tmp_path):
    """A bundle that's only been resolved/planned (no emit) shouldn't be
    submittable — emit produces manifest.json and the Cypher files."""
    bundle_dir = tmp_path / "out"
    bundle_dir.mkdir()
    # routing.yaml exists but no manifest.json (no emit).
    (bundle_dir / "routing.yaml").write_text(
        yaml.safe_dump({"destination": "micromap-core", "organization_id": "x",
                        "provenance": {"contributor": "x", "submitted_at": "now",
                                       "mapping_version": "sha256:abc"},
                        "federation_endpoint": {"type": None, "uri": None}}),
        encoding="utf-8",
    )

    runner = CliRunner()
    with patch("micromap_mapforge.cli._executor_for_bundle") as factory:
        result = runner.invoke(
            main,
            ["submit", "--bundle", str(bundle_dir),
             "--neo4j-uri", "bolt://localhost", "--neo4j-user", "u",
             "--neo4j-password", "p"],
        )
    assert result.exit_code != 0
    assert "manifest" in result.output.lower() or "emit" in result.output.lower()
    assert not factory.called


def test_submit_unapproved_bundle_refuses_without_force(tmp_path):
    """A successfully emitted but un-approved bundle must refuse to submit
    unless --force is explicitly passed. Pin this; the contract is what
    makes the approval gate meaningful."""
    bundle_dir = _seed_emitted_bundle(tmp_path)
    # Forcibly un-approve the manifest (the fixture approves it; revert for
    # this test).
    manifest_path = bundle_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["approved"] = False
    manifest.pop("reviewer", None)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    runner = CliRunner()
    with patch("micromap_mapforge.cli._executor_for_bundle") as factory:
        result = runner.invoke(
            main,
            ["submit", "--bundle", str(bundle_dir), "--reviewer", "alice",
             "--neo4j-uri", "bolt://localhost", "--neo4j-user", "u",
             "--neo4j-password", "p"],
        )
    assert result.exit_code != 0
    assert "approved" in result.output.lower() or "--force" in result.output.lower()
    assert not factory.called
