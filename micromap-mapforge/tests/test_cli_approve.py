import json
from pathlib import Path

import yaml
from click.testing import CliRunner

from micromap_mapforge.cli import main


def _seed(tmp_path: Path) -> Path:
    out = tmp_path / "out"
    out.mkdir()
    # Create the source file so emit can load rows.
    src = tmp_path / "s.tsv"
    src.write_text("col\nval\n", encoding="utf-8")
    (out / "mapping.yaml").write_text(yaml.safe_dump({
        "source": {"name": "s", "format": "tsv", "path": str(src)},
        "entities": [], "relationships": [],
    }), encoding="utf-8")
    (out / "routing.yaml").write_text(yaml.safe_dump({
        "destination": "micromap-core", "organization_id": "o",
        "provenance": {"contributor": "o", "submitted_at": "now", "mapping_version": "sha256:x"},
        "federation_endpoint": {"type": None, "uri": None},
    }), encoding="utf-8")
    (out / "resolution.json").write_text(json.dumps({
        "resolved": [], "unresolved": [], "ambiguous": [],
        "resolved_count": 0, "unresolved_count": 0, "ambiguous_count": 0,
    }), encoding="utf-8")
    return out


def test_approve_flips_manifest_flag(tmp_path):
    bundle_dir = _seed(tmp_path)
    runner = CliRunner()
    # Must emit first so manifest.json exists.
    runner.invoke(main, ["emit", "--bundle", str(bundle_dir)])
    pre = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert pre["approved"] is False

    res = runner.invoke(main, ["approve", "--bundle", str(bundle_dir), "--reviewer", "alice"])
    assert res.exit_code == 0, res.output

    post = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert post["approved"] is True
    assert post["reviewer"] == "alice"
    assert "approved_at" in post


def test_approve_requires_manifest(tmp_path):
    bundle_dir = _seed(tmp_path)
    runner = CliRunner()
    # Do NOT emit first, so no manifest.json.
    res = runner.invoke(main, ["approve", "--bundle", str(bundle_dir), "--reviewer", "alice"])
    assert res.exit_code != 0
    assert "manifest" in res.output.lower() or "emit" in res.output.lower()
