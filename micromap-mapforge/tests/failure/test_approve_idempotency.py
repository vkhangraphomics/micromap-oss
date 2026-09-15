"""Idempotency contract for the `approve` step (issue #62 scope).

The `approve` command flips manifest.json's `approved` flag and records the
reviewer + timestamp. Running it twice on the same bundle is a documented
recovery path (an operator re-runs after fixing a transient error), so it
must remain safe:

- second run completes cleanly (no error / non-zero exit)
- final state is approved=True with the second reviewer recorded
- approved_at on the second run is >= the first
- the on-disk manifest stays valid JSON (no half-written files)

True end-to-end idempotency (Contribution-node-write is single-effect across
re-runs) lives with the integration suite; that needs a live Neo4j.
"""

import json
from pathlib import Path
from datetime import datetime

import yaml
from click.testing import CliRunner

from micromap_mapforge.cli import main


def _seed_emitted_bundle(tmp_path: Path) -> Path:
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
    return out


def test_approve_twice_does_not_fail(tmp_path):
    bundle_dir = _seed_emitted_bundle(tmp_path)

    runner = CliRunner()
    first = runner.invoke(main, ["approve", "--bundle", str(bundle_dir),
                                 "--reviewer", "alice"])
    assert first.exit_code == 0, first.output

    second = runner.invoke(main, ["approve", "--bundle", str(bundle_dir),
                                  "--reviewer", "bob"])
    assert second.exit_code == 0, second.output


def test_approve_twice_keeps_manifest_approved(tmp_path):
    bundle_dir = _seed_emitted_bundle(tmp_path)

    runner = CliRunner()
    runner.invoke(main, ["approve", "--bundle", str(bundle_dir),
                         "--reviewer", "alice"])
    runner.invoke(main, ["approve", "--bundle", str(bundle_dir),
                         "--reviewer", "bob"])

    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["approved"] is True
    # Second approver wins — pin this so re-approval semantics are clear.
    assert manifest["reviewer"] == "bob"


def test_approve_twice_updates_timestamp_forward(tmp_path):
    bundle_dir = _seed_emitted_bundle(tmp_path)

    runner = CliRunner()
    runner.invoke(main, ["approve", "--bundle", str(bundle_dir),
                         "--reviewer", "alice"])
    first_ts = json.loads(
        (bundle_dir / "manifest.json").read_text(encoding="utf-8")
    )["approved_at"]

    runner.invoke(main, ["approve", "--bundle", str(bundle_dir),
                         "--reviewer", "bob"])
    second_ts = json.loads(
        (bundle_dir / "manifest.json").read_text(encoding="utf-8")
    )["approved_at"]

    # The second approval's timestamp must be >= the first (forward-moving).
    assert datetime.fromisoformat(second_ts) >= datetime.fromisoformat(first_ts)


def test_approve_leaves_manifest_as_valid_json(tmp_path):
    """No half-written / truncated state after re-run."""
    bundle_dir = _seed_emitted_bundle(tmp_path)

    runner = CliRunner()
    runner.invoke(main, ["approve", "--bundle", str(bundle_dir),
                         "--reviewer", "alice"])
    runner.invoke(main, ["approve", "--bundle", str(bundle_dir),
                         "--reviewer", "bob"])

    raw = (bundle_dir / "manifest.json").read_text(encoding="utf-8")
    parsed = json.loads(raw)  # must not raise
    assert isinstance(parsed, dict)


def test_approve_on_missing_manifest_fails_cleanly(tmp_path):
    """An operator runs `approve` before `emit`. Must exit non-zero with a
    clean message — not stack-trace a JSONDecodeError."""
    bundle_dir = tmp_path / "no-emit"
    bundle_dir.mkdir()

    runner = CliRunner()
    result = runner.invoke(main, ["approve", "--bundle", str(bundle_dir),
                                  "--reviewer", "alice"])
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "manifest" in result.output.lower() or "emit" in result.output.lower()
