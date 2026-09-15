import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from click.testing import CliRunner

from micromap_mapforge.cli import main


def _seed_bundle(tmp_path: Path) -> Path:
    """Minimal bundle after inspect → map → resolve → plan."""
    out = tmp_path / "out"
    out.mkdir(parents=True)
    (out / "mapping.yaml").write_text(yaml.safe_dump({
        "source": {"name": "s", "format": "tsv", "path": "s.tsv"},
        "entities": [
            {"label": "Taxon", "match_on": "ncbi_tax_id",
             "columns": {"ncbi_tax_id": "tax_id"}, "confidence": "EXTRACTED"},
        ],
        "relationships": [],
    }), encoding="utf-8")
    (out / "routing.yaml").write_text(yaml.safe_dump({
        "destination": "micromap-core",
        "organization_id": "org-xyz",
        "provenance": {"contributor": "org-xyz", "submitted_at": "now", "mapping_version": "sha256:abc"},
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
    # Source file so emit can load rows
    (tmp_path / "s.tsv").write_text("tax_id\torganism\n562\tEscherichia coli\n", encoding="utf-8")
    # Update mapping to point at actual file
    mapping = yaml.safe_load((out / "mapping.yaml").read_text())
    mapping["source"]["path"] = str(tmp_path / "s.tsv")
    (out / "mapping.yaml").write_text(yaml.safe_dump(mapping), encoding="utf-8")
    return out


def test_cli_emit_produces_cypher_and_manifest(tmp_path):
    """3b-3a (#127): `emit` is now the IR-routed path unconditionally
    (legacy `emit/cypher.generate_cypher` retired). For a resolved Taxon
    the serializer emits MATCH on the resolver's native merge_field with
    no org-scope (PR #108 / issue #90 behavior preserved via the 3b-0
    discriminator)."""
    bundle_dir = _seed_bundle(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["emit", "--bundle", str(bundle_dir)])
    assert result.exit_code == 0, result.output
    assert (bundle_dir / "cypher" / "nodes_Taxon.cypher").exists()
    assert (bundle_dir / "manifest.json").exists()
    assert (bundle_dir / "INGEST_REPORT.md").exists()
    cy = (bundle_dir / "cypher" / "nodes_Taxon.cypher").read_text(encoding="utf-8")
    assert "MATCH (n:Taxon {ncbi_tax_id: row.merge_value})" in cy
    params = json.loads((bundle_dir / "cypher" / "nodes_Taxon.params.json").read_text(encoding="utf-8"))
    assert "batch_ncbi_tax_id" in params
    assert params["batch_ncbi_tax_id"][0]["merge_value"] == "562"


def test_cli_emit_honors_provenance_enabled_false(tmp_path):
    """3b-2 (#126): when routing.yaml carries `provenance.enabled: false`,
    `emit` produces a bundle with no `provenance_*` props on node/edge
    Neo4j payloads. Confidence remains (it's not provenance). Extends
    PR #113's Contribution-node opt-out down to edge-level properties."""
    bundle_dir = _seed_bundle(tmp_path)
    routing = yaml.safe_load((bundle_dir / "routing.yaml").read_text(encoding="utf-8"))
    routing["provenance"] = {"enabled": False, "contributor": routing["provenance"]["contributor"]}
    (bundle_dir / "routing.yaml").write_text(yaml.safe_dump(routing), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, ["emit", "--bundle", str(bundle_dir)])
    assert result.exit_code == 0, result.output

    params = json.loads((bundle_dir / "cypher" / "nodes_Taxon.params.json").read_text(encoding="utf-8"))
    props = params["batch_ncbi_tax_id"][0]["props"]
    assert "provenance_source" not in props
    assert "provenance_method" not in props
    assert "provenance_ref" not in props


def test_cli_emit_ir_flag_was_retired(tmp_path):
    """Regression guard: 3b-3a (#127) deleted the --ir flag. Passing it
    should fail with Click's 'no such option' error, not silently succeed
    or fall back to a now-deleted code path."""
    bundle_dir = _seed_bundle(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["emit", "--ir", "--bundle", str(bundle_dir)])
    assert result.exit_code != 0
    assert "no such option" in result.output.lower() or "--ir" in result.output


def test_cli_submit_calls_executor(tmp_path):
    bundle_dir = _seed_bundle(tmp_path)
    runner = CliRunner()
    # First emit
    runner.invoke(main, ["emit", "--bundle", str(bundle_dir)])
    # Then submit — patch the driver factory AND the executor to observe calls
    fake_receipt = MagicMock(success=True, destination="micromap-core",
                             nodes_written=1, relationships_written=0, notes="")
    fake_executor = MagicMock()
    fake_executor.submit.return_value = fake_receipt
    with patch("micromap_mapforge.cli._executor_for_bundle", return_value=(fake_executor, None)), \
         patch("micromap_mapforge.cli._write_contribution_for_bundle"):
        result = runner.invoke(
            main,
            ["submit", "--bundle", str(bundle_dir), "--force",
             "--reviewer", "alice",
             "--neo4j-uri", "bolt://localhost", "--neo4j-user", "u", "--neo4j-password", "p"],
        )
    assert result.exit_code == 0, result.output
    assert fake_executor.submit.called


def test_cli_submit_requires_approval_manifest(tmp_path):
    bundle_dir = _seed_bundle(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["emit", "--bundle", str(bundle_dir)])
    # manifest.json has approved=False by default. Submit should refuse unless --force.
    with patch("micromap_mapforge.cli._executor_for_bundle") as factory:
        result = runner.invoke(
            main,
            ["submit", "--bundle", str(bundle_dir),
             "--neo4j-uri", "bolt://localhost", "--neo4j-user", "u", "--neo4j-password", "p"],
        )
    assert result.exit_code != 0
    assert "approved" in result.output.lower() or "--force" in result.output.lower()
    assert not factory.called


def test_cli_submit_accepts_force(tmp_path):
    bundle_dir = _seed_bundle(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["emit", "--bundle", str(bundle_dir)])
    fake_receipt = MagicMock(success=True, destination="micromap-core",
                             nodes_written=1, relationships_written=0, notes="")
    fake_executor = MagicMock()
    fake_executor.submit.return_value = fake_receipt
    with patch("micromap_mapforge.cli._executor_for_bundle", return_value=(fake_executor, None)), \
         patch("micromap_mapforge.cli._write_contribution_for_bundle"):
        result = runner.invoke(
            main,
            ["submit", "--bundle", str(bundle_dir), "--force",
             "--reviewer", "alice",
             "--neo4j-uri", "bolt://localhost", "--neo4j-user", "u", "--neo4j-password", "p"],
        )
    assert result.exit_code == 0, result.output
