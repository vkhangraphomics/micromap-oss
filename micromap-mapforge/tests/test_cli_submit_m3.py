import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from click.testing import CliRunner

from micromap_mapforge.cli import main


def _seed(tmp_path: Path, destination: str = "micromap-core") -> Path:
    out = tmp_path / "out"
    out.mkdir()
    (out / "mapping.yaml").write_text(yaml.safe_dump({
        "source": {"name": "s", "format": "tsv", "path": str(tmp_path / "s.tsv")},
        "entities": [{"label": "Taxon", "match_on": "ncbi_tax_id",
                      "columns": {"ncbi_tax_id": "tax_id"}, "confidence": "EXTRACTED"}],
        "relationships": [],
    }), encoding="utf-8")
    (out / "routing.yaml").write_text(yaml.safe_dump({
        "destination": destination,
        "organization_id": "org-xyz",
        "provenance": {"contributor": "org-xyz", "submitted_at": "now", "mapping_version": "sha256:abc"},
        "federation_endpoint": {"type": None, "uri": None},
    }), encoding="utf-8")
    (out / "resolution.json").write_text(json.dumps({
        "resolved": [{"entity_label": "Taxon", "source_term": "562", "candidates": [
            {"node_id": "ncbi_tax_id:562", "match_type": "EXTRACTED", "score": 1.0, "reason": "",
             "merge_field": "ncbi_tax_id", "merge_value": "562"},
        ]}],
        "unresolved": [], "ambiguous": [],
        "resolved_count": 1, "unresolved_count": 0, "ambiguous_count": 0,
    }), encoding="utf-8")
    (tmp_path / "s.tsv").write_text("tax_id\n562\n", encoding="utf-8")
    return out


def _invoke_emit(runner, bundle_dir):
    runner.invoke(main, ["emit", "--bundle", str(bundle_dir)])


def test_submit_writes_contribution_for_core_destination(tmp_path):
    bundle_dir = _seed(tmp_path)
    runner = CliRunner()
    _invoke_emit(runner, bundle_dir)

    fake_receipt = MagicMock(success=True, destination="micromap-core",
                             nodes_written=1, relationships_written=0, notes="")
    fake_executor = MagicMock(submit=MagicMock(return_value=fake_receipt))
    fake_contribution_writer = MagicMock()

    with patch("micromap_mapforge.cli._executor_for_bundle", return_value=(fake_executor, None)), \
         patch("micromap_mapforge.cli._write_contribution_for_bundle",
               side_effect=fake_contribution_writer):
        result = runner.invoke(main, [
            "submit", "--bundle", str(bundle_dir), "--force",
            "--neo4j-uri", "bolt://localhost", "--neo4j-user", "u", "--neo4j-password", "p",
            "--reviewer", "alice",
        ])
    assert result.exit_code == 0, result.output
    assert fake_executor.submit.called
    assert fake_contribution_writer.called


def test_submit_writes_contribution_for_registry_only(tmp_path):
    bundle_dir = _seed(tmp_path, destination="registry-only")
    runner = CliRunner()
    _invoke_emit(runner, bundle_dir)

    fake_receipt = MagicMock(success=True, destination="registry-only",
                             nodes_written=0, relationships_written=0, notes="")
    fake_executor = MagicMock(submit=MagicMock(return_value=fake_receipt))
    fake_contribution_writer = MagicMock()

    with patch("micromap_mapforge.cli._executor_for_bundle", return_value=(fake_executor, None)), \
         patch("micromap_mapforge.cli._write_contribution_for_bundle",
               side_effect=fake_contribution_writer):
        result = runner.invoke(main, [
            "submit", "--bundle", str(bundle_dir), "--force",
            "--neo4j-uri", "bolt://localhost", "--neo4j-user", "u", "--neo4j-password", "p",
            "--reviewer", "alice",
        ])
    assert result.exit_code == 0, result.output
    # Contribution still writes for non-core destinations
    assert fake_contribution_writer.called


def test_submit_requires_reviewer_when_writing_contribution(tmp_path):
    bundle_dir = _seed(tmp_path)
    runner = CliRunner()
    _invoke_emit(runner, bundle_dir)

    with patch("micromap_mapforge.cli._executor_for_bundle") as factory:
        result = runner.invoke(main, [
            "submit", "--bundle", str(bundle_dir), "--force",
            "--neo4j-uri", "bolt://localhost", "--neo4j-user", "u", "--neo4j-password", "p",
        ])
    assert result.exit_code != 0
    assert "reviewer" in result.output.lower()
    # Pre-flight refused before factory was invoked
    assert not factory.called


def test_write_contribution_resolves_relative_source_path(tmp_path):
    """Regression: source_path from mapping was previously resolved against CWD,
    causing source_sha256 to be empty when CWD didn't match. Now resolves
    relative paths against the bundle directory."""
    from unittest.mock import MagicMock, patch

    from micromap_mapforge.cli import _write_contribution_for_bundle

    out = tmp_path / "out"
    out.mkdir()
    # mapping.yaml has a RELATIVE source path
    (out / "mapping.yaml").write_text(
        yaml.safe_dump({
            "source": {"name": "s", "format": "tsv", "path": "study.tsv"},
            "entities": [], "relationships": [],
        }),
        encoding="utf-8",
    )
    (out / "resolution.json").write_text(json.dumps({
        "resolved": [], "unresolved": [], "ambiguous": [],
        "resolved_count": 0, "unresolved_count": 0, "ambiguous_count": 0,
    }), encoding="utf-8")
    # Source file lives in the bundle directory (relative to mapping.yaml).
    (out / "study.tsv").write_text("tax_id\n562\n", encoding="utf-8")

    routing = {
        "destination": "registry-only",
        "organization_id": "org-xyz",
        "provenance": {"contributor": "org-xyz"},
    }
    fake_receipt = MagicMock(success=True, destination="registry-only",
                             nodes_written=0, relationships_written=0, notes="")

    captured_records = []

    def _capture(driver, record, database="neo4j"):
        captured_records.append(record)

    fake_driver = MagicMock()
    with patch("neo4j.GraphDatabase.driver", return_value=fake_driver), \
         patch("micromap_mapforge.provenance.contribution.write_contribution",
               side_effect=_capture):
        _write_contribution_for_bundle(
            bundle_dir=out,
            routing=routing,
            reviewer="alice",
            receipt=fake_receipt,
            neo4j_uri="bolt://localhost",
            neo4j_user="u",
            neo4j_password="p",
            neo4j_database="neo4j",
        )

    assert len(captured_records) == 1
    rec = captured_records[0]
    # source_sha256 should be a real sha256 hex digest, NOT empty
    assert rec.source_sha256 != ""
    assert len(rec.source_sha256) == 64   # sha256 hex


# --- #63: provenance opt-out ---

def _seed_disabled(tmp_path: Path, destination: str = "micromap-core") -> Path:
    """Seed a bundle whose routing.yaml has provenance.enabled = False."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "mapping.yaml").write_text(yaml.safe_dump({
        "source": {"name": "s", "format": "tsv", "path": str(tmp_path / "s.tsv")},
        "entities": [{"label": "Taxon", "match_on": "ncbi_tax_id",
                      "columns": {"ncbi_tax_id": "tax_id"}, "confidence": "EXTRACTED"}],
        "relationships": [],
    }), encoding="utf-8")
    (out / "routing.yaml").write_text(yaml.safe_dump({
        "destination": destination,
        "organization_id": "org-xyz",
        "provenance": {
            "contributor": "org-xyz",
            "submitted_at": "now",
            "mapping_version": "sha256:abc",
            "enabled": False,             # <- opt-out
        },
        "federation_endpoint": {"type": None, "uri": None},
    }), encoding="utf-8")
    (out / "resolution.json").write_text(json.dumps({
        "resolved": [{"entity_label": "Taxon", "source_term": "562", "candidates": [
            {"node_id": "ncbi_tax_id:562", "match_type": "EXTRACTED", "score": 1.0, "reason": "",
             "merge_field": "ncbi_tax_id", "merge_value": "562"},
        ]}],
        "unresolved": [], "ambiguous": [],
        "resolved_count": 1, "unresolved_count": 0, "ambiguous_count": 0,
    }), encoding="utf-8")
    (tmp_path / "s.tsv").write_text("tax_id\n562\n", encoding="utf-8")
    return out


def test_submit_skips_contribution_when_provenance_disabled(tmp_path):
    bundle_dir = _seed_disabled(tmp_path)
    runner = CliRunner()
    _invoke_emit(runner, bundle_dir)

    fake_receipt = MagicMock(success=True, destination="micromap-core",
                             nodes_written=1, relationships_written=0, notes="")
    fake_executor = MagicMock(submit=MagicMock(return_value=fake_receipt))
    fake_contribution_writer = MagicMock()

    with patch("micromap_mapforge.cli._executor_for_bundle", return_value=(fake_executor, None)), \
         patch("micromap_mapforge.cli._write_contribution_for_bundle",
               side_effect=fake_contribution_writer):
        result = runner.invoke(main, [
            "submit", "--bundle", str(bundle_dir), "--force",
            "--neo4j-uri", "bolt://localhost", "--neo4j-user", "u", "--neo4j-password", "p",
            # No --reviewer -- should not be required.
        ])
    assert result.exit_code == 0, result.output
    assert fake_executor.submit.called
    assert not fake_contribution_writer.called, (
        "Contribution writer must NOT be called when provenance.enabled is False"
    )
    # Stderr note (CliRunner merges stderr into result.output by default).
    assert "provenance writer disabled" in result.output.lower()


def test_submit_does_not_require_reviewer_when_disabled(tmp_path):
    """Without --reviewer and without manifest reviewer, submit still succeeds."""
    bundle_dir = _seed_disabled(tmp_path)
    runner = CliRunner()
    _invoke_emit(runner, bundle_dir)

    fake_receipt = MagicMock(success=True, destination="micromap-core",
                             nodes_written=1, relationships_written=0, notes="")
    fake_executor = MagicMock(submit=MagicMock(return_value=fake_receipt))

    with patch("micromap_mapforge.cli._executor_for_bundle", return_value=(fake_executor, None)), \
         patch("micromap_mapforge.cli._write_contribution_for_bundle"):
        result = runner.invoke(main, [
            "submit", "--bundle", str(bundle_dir), "--force",
            "--neo4j-uri", "bolt://localhost", "--neo4j-user", "u", "--neo4j-password", "p",
        ])
    assert result.exit_code == 0, result.output
    # Confirm the reviewer-required exit-8 path did NOT fire.
    assert "reviewer" not in result.output.lower(), (
        "reviewer-required error should not fire when provenance is disabled; "
        f"got output: {result.output!r}"
    )


def test_submit_warns_when_reviewer_passed_but_provenance_disabled(tmp_path):
    bundle_dir = _seed_disabled(tmp_path)
    runner = CliRunner()
    _invoke_emit(runner, bundle_dir)

    fake_receipt = MagicMock(success=True, destination="micromap-core",
                             nodes_written=1, relationships_written=0, notes="")
    fake_executor = MagicMock(submit=MagicMock(return_value=fake_receipt))
    fake_contribution_writer = MagicMock()

    with patch("micromap_mapforge.cli._executor_for_bundle", return_value=(fake_executor, None)), \
         patch("micromap_mapforge.cli._write_contribution_for_bundle",
               side_effect=fake_contribution_writer):
        result = runner.invoke(main, [
            "submit", "--bundle", str(bundle_dir), "--force",
            "--neo4j-uri", "bolt://localhost", "--neo4j-user", "u", "--neo4j-password", "p",
            "--reviewer", "alice",
        ])
    assert result.exit_code == 0, result.output
    assert not fake_contribution_writer.called
    assert "--reviewer ignored" in result.output.lower()
