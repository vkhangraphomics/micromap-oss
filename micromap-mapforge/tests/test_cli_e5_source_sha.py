"""CLI tests for E5: source_sha256 sealing at map time + submit hardening (#78).

Four scenarios pin the contract:
1. Fresh bundle (post-E5): mapping.yaml carries source.sha256; submit reads it.
2. Source absent at submit (post-E5 bundle): submit still succeeds because
   sha256 is sealed in mapping.yaml.
3. Legacy bundle (pre-E5, no source.sha256) with source file present:
   submit recomputes from file. Back-compat path.
4. Legacy bundle with source file ABSENT: submit errors loudly (no silent
   empty-string fallback that corrupts the audit trail).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from micromap_mapforge.cli import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _build_post_e5_bundle(tmp_path: Path, runner: CliRunner) -> tuple[Path, Path, str]:
    """Build a bundle the normal way (post-E5: mapping.yaml has source.sha256).

    Returns (source_csv_path, bundle_dir, expected_sha).
    """
    csv = tmp_path / "data.csv"
    csv.write_text(
        "gene_symbol,clinvar_id\nBRCA1,RCV000077444\n", encoding="utf-8",
    )
    expected_sha = hashlib.sha256(csv.read_bytes()).hexdigest()
    out = tmp_path / "bundle"

    result = runner.invoke(main, [
        "map", str(csv),
        "--out", str(out),
        "--schema-config", "genomics",
    ])
    assert result.exit_code == 0, result.output
    return csv, out, expected_sha


# ---------------------------------------------------------------------------
# Test 1: fresh bundle seals SHA into mapping.yaml
# ---------------------------------------------------------------------------


def test_map_writes_source_sha256_into_mapping_yaml(tmp_path, runner):
    """mapforge map seals sha256(source) into mapping.yaml::source.sha256."""
    _csv, out, expected_sha = _build_post_e5_bundle(tmp_path, runner)
    mapping = yaml.safe_load((out / "mapping.yaml").read_text(encoding="utf-8"))
    assert mapping["source"].get("sha256") == expected_sha


# ---------------------------------------------------------------------------
# Test 2: post-E5 bundle, source missing at submit -- sealed SHA wins
# ---------------------------------------------------------------------------


def test_write_contribution_reads_sealed_sha_when_source_missing(tmp_path, runner):
    """The _resolve_source_sha_for_submit helper returns the sealed sha
    without touching the source file, even if the source is gone.

    Pins the core E5 contract: bundles are portable across consumers
    (Workbench-staged paths -> local submit, etc.) because submit reads
    the sealed value rather than re-opening the source.
    """
    from micromap_mapforge.cli import _resolve_source_sha_for_submit

    csv, bundle_dir, expected_sha = _build_post_e5_bundle(tmp_path, runner)

    # Delete the source file BEFORE we read the SHA from the bundle.
    csv.unlink()
    assert not csv.exists()

    # mapping.yaml still carries the sealed sha.
    mapping = yaml.safe_load((bundle_dir / "mapping.yaml").read_text(encoding="utf-8"))
    stored_sha = mapping["source"].get("sha256")
    assert stored_sha == expected_sha, (
        f"sealed sha was lost: stored={stored_sha!r} expected={expected_sha!r}"
    )

    # The helper returns the sealed sha without touching the (missing) source.
    # This is the actual E5 contract: submit no longer depends on the source
    # file being present at the recorded path.
    resolved = _resolve_source_sha_for_submit(bundle_dir, mapping)
    assert resolved == expected_sha, (
        f"helper failed to return sealed sha: resolved={resolved!r} "
        f"expected={expected_sha!r}"
    )


# ---------------------------------------------------------------------------
# Test 3: legacy bundle (pre-E5) with source file present -- recompute
# ---------------------------------------------------------------------------


def test_write_contribution_recomputes_for_legacy_bundle_with_source(tmp_path):
    """A pre-E5 bundle (no source.sha256 in mapping.yaml) with the source
    file still present submits cleanly -- the helper recomputes the SHA at
    submit time from the on-disk file. Back-compat path."""
    from micromap_mapforge.cli import _resolve_source_sha_for_submit

    # Construct a legacy-style bundle by hand: mapping.yaml without sha256.
    bundle = tmp_path / "legacy-bundle"
    bundle.mkdir()
    source = bundle / "source.csv"
    source.write_text("gene_symbol\nBRCA1\n", encoding="utf-8")
    expected_sha = hashlib.sha256(source.read_bytes()).hexdigest()

    mapping = {
        "source": {
            "name": "source",
            "format": "csv",
            "path": "source.csv",  # bundle-relative; helper resolves it
        },
        "entities": [{"label": "Gene", "match_on": "symbol",
                      "columns": {"symbol": "gene_symbol"}}],
        "relationships": [],
    }
    (bundle / "mapping.yaml").write_text(
        yaml.safe_dump(mapping), encoding="utf-8",
    )

    sha = _resolve_source_sha_for_submit(bundle, mapping)
    assert sha == expected_sha


# ---------------------------------------------------------------------------
# Test 4: legacy bundle + source missing -- LOUD ERROR (no silent corruption)
# ---------------------------------------------------------------------------


def test_write_contribution_errors_for_legacy_bundle_without_source(tmp_path):
    """A pre-E5 bundle whose source file is also gone fails LOUDLY rather
    than silently substituting empty source_sha256.

    Before E5, this scenario produced a warning + empty string, which let
    Contribution MERGEs collide silently. After E5, it raises
    click.UsageError -- the loud failure mode is the right one for an
    audit-trail field.
    """
    import click
    from micromap_mapforge.cli import _resolve_source_sha_for_submit

    bundle = tmp_path / "legacy-bundle"
    bundle.mkdir()
    # No source file; mapping.yaml has no sha256 field.
    mapping = {
        "source": {
            "name": "source",
            "format": "csv",
            "path": "source.csv",  # bundle-relative; file deliberately absent
        },
        "entities": [{"label": "Gene", "match_on": "symbol",
                      "columns": {"symbol": "gene_symbol"}}],
        "relationships": [],
    }
    (bundle / "mapping.yaml").write_text(
        yaml.safe_dump(mapping), encoding="utf-8",
    )

    with pytest.raises(click.UsageError, match="source_sha256 is not sealed"):
        _resolve_source_sha_for_submit(bundle, mapping)


# ---------------------------------------------------------------------------
# Tests for _emit_via_ir sealed-sha preference (#158)
# ---------------------------------------------------------------------------


def test_emit_via_ir_file_prefers_sealed_sha(tmp_path, monkeypatch):
    """_emit_via_ir passes the SEALED sha to tabular_to_ir, not file bytes hash.

    Post-E5, mapping.yaml carries source.sha256. If the source file is moved
    or modified between `map` and `emit`, the SourceRef must still carry the
    sealed audit value — not a freshly-recomputed hash that diverges from
    the sealed one.
    """
    import micromap_mapforge.integration.tabular as tabular_mod
    import micromap_mapforge.integration.biocypher.serialize as serialize_mod
    from micromap_mapforge.cli import _emit_via_ir
    from micromap_mapforge.resolve.pipeline import ResolutionReport

    # Create a real CSV so the path exists (fallback branch would read it).
    csv = tmp_path / "source.csv"
    csv.write_text("col\nval\n", encoding="utf-8")

    # sealed_sha is deliberately different from sha256(file bytes) so the test
    # proves the sealed value is used, not the recomputed one.
    sealed_sha = "b" * 64
    real_sha = hashlib.sha256(csv.read_bytes()).hexdigest()
    assert sealed_sha != real_sha, "test setup error: values must differ"

    mapping = {
        "source": {
            "name": "test",
            "format": "csv",
            "path": "source.csv",
            "sha256": sealed_sha,   # sealed at map time
        },
        "entities": [],
        "relationships": [],
    }

    captured: dict = {}

    def fake_tabular_to_ir(**kwargs):
        captured["source_ref"] = kwargs["source_ref"]
        return {
            "nodes": [],
            "edges": [],
            "schema_config": {},
            "provenance": {},
            "confidence": "high",
            "tier_tags": [],
            "organization_id": "test-org",
        }

    monkeypatch.setattr(tabular_mod, "tabular_to_ir", fake_tabular_to_ir)
    monkeypatch.setattr(serialize_mod, "serialize_bundle", lambda *a, **kw: None)

    _emit_via_ir(
        mapping=mapping,
        rows=[],
        report=ResolutionReport(),
        organization_id="test-org",
        bundle_dir=tmp_path,
    )

    assert "source_ref" in captured, "_emit_via_ir never called tabular_to_ir"
    assert captured["source_ref"].sha256 == sealed_sha, (
        f"expected sealed sha {sealed_sha!r}, got {captured['source_ref'].sha256!r}"
    )


def test_emit_via_ir_file_fallsback_when_no_sealed_sha(tmp_path, monkeypatch):
    """_emit_via_ir falls back to sha256(file bytes) when no sealed sha present.

    Pre-E5 bundles have no source.sha256 in mapping.yaml. The function must
    recompute from the source file as before — existing behaviour is preserved.
    """
    import micromap_mapforge.integration.tabular as tabular_mod
    import micromap_mapforge.integration.biocypher.serialize as serialize_mod
    from micromap_mapforge.cli import _emit_via_ir
    from micromap_mapforge.resolve.pipeline import ResolutionReport

    csv = tmp_path / "source.csv"
    csv.write_text("col\nval\n", encoding="utf-8")
    expected_sha = hashlib.sha256(csv.read_bytes()).hexdigest()

    # No sha256 key — pre-E5 mapping.
    mapping = {
        "source": {
            "name": "test",
            "format": "csv",
            "path": "source.csv",
        },
        "entities": [],
        "relationships": [],
    }

    captured: dict = {}

    def fake_tabular_to_ir(**kwargs):
        captured["source_ref"] = kwargs["source_ref"]
        return {
            "nodes": [],
            "edges": [],
            "schema_config": {},
            "provenance": {},
            "confidence": "high",
            "tier_tags": [],
            "organization_id": "test-org",
        }

    monkeypatch.setattr(tabular_mod, "tabular_to_ir", fake_tabular_to_ir)
    monkeypatch.setattr(serialize_mod, "serialize_bundle", lambda *a, **kw: None)

    _emit_via_ir(
        mapping=mapping,
        rows=[],
        report=ResolutionReport(),
        organization_id="test-org",
        bundle_dir=tmp_path,
    )

    assert "source_ref" in captured, "_emit_via_ir never called tabular_to_ir"
    assert captured["source_ref"].sha256 == expected_sha, (
        f"expected file-bytes sha {expected_sha!r}, got {captured['source_ref'].sha256!r}"
    )


def test_emit_via_ir_dir_prefers_sealed_sha(tmp_path, monkeypatch):
    """_emit_via_ir passes the SEALED sha for a directory source too.

    The dir branch (source_path.is_dir()) must prefer the sealed value from
    mapping.yaml::source.sha256 over computing a tree-hash from disk.
    """
    import micromap_mapforge.integration.tabular as tabular_mod
    import micromap_mapforge.integration.biocypher.serialize as serialize_mod
    from micromap_mapforge.cli import _emit_via_ir
    from micromap_mapforge.resolve.pipeline import ResolutionReport

    # Create a real directory with a file so the tree-hash fallback would work.
    source_dir = tmp_path / "source_dir"
    source_dir.mkdir()
    (source_dir / "data.csv").write_text("col\nval\n", encoding="utf-8")

    sealed_sha = "c" * 64

    mapping = {
        "source": {
            "name": "test-dir",
            "format": "csv",
            "path": "source_dir",
            "sha256": sealed_sha,
        },
        "entities": [],
        "relationships": [],
    }

    captured: dict = {}

    def fake_tabular_to_ir(**kwargs):
        captured["source_ref"] = kwargs["source_ref"]
        return {
            "nodes": [],
            "edges": [],
            "schema_config": {},
            "provenance": {},
            "confidence": "high",
            "tier_tags": [],
            "organization_id": "test-org",
        }

    monkeypatch.setattr(tabular_mod, "tabular_to_ir", fake_tabular_to_ir)
    monkeypatch.setattr(serialize_mod, "serialize_bundle", lambda *a, **kw: None)

    _emit_via_ir(
        mapping=mapping,
        rows=[],
        report=ResolutionReport(),
        organization_id="test-org",
        bundle_dir=tmp_path,
    )

    assert "source_ref" in captured, "_emit_via_ir never called tabular_to_ir"
    assert captured["source_ref"].sha256 == sealed_sha, (
        f"expected sealed sha {sealed_sha!r}, got {captured['source_ref'].sha256!r}"
    )


def test_emit_via_ir_dir_fallsback_when_no_sealed_sha(tmp_path, monkeypatch):
    """_emit_via_ir falls back to tree-hash for a directory source with no sealed sha.

    Pre-E5 bundles have no source.sha256. For a directory source the fallback
    is a tree-hash of sorted (relpath, sha256) file entries, not file bytes.
    The sealed-prefer check must not break this pre-existing fallback path.
    """
    import hashlib as _hashlib
    import micromap_mapforge.integration.tabular as tabular_mod
    import micromap_mapforge.integration.biocypher.serialize as serialize_mod
    from micromap_mapforge.cli import _emit_via_ir
    from micromap_mapforge.resolve.pipeline import ResolutionReport

    source_dir = tmp_path / "source_dir"
    source_dir.mkdir()
    f = source_dir / "data.csv"
    f.write_text("col\nval\n", encoding="utf-8")

    # Compute the expected tree-hash independently (same algorithm as _emit_via_ir).
    # Tree-hash = sha256(join([f"{rel}:{sha256(file)}" for each file], "\n"))
    rel = f.relative_to(source_dir).as_posix()
    file_sha = _hashlib.sha256(f.read_bytes()).hexdigest()
    parts = [f"{rel}:{file_sha}"]
    expected_sha = _hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()

    # No sha256 key — pre-E5 mapping.
    mapping = {
        "source": {
            "name": "test-dir",
            "format": "csv",
            "path": "source_dir",
        },
        "entities": [],
        "relationships": [],
    }

    captured: dict = {}

    def fake_tabular_to_ir(**kwargs):
        captured["source_ref"] = kwargs["source_ref"]
        return {
            "nodes": [],
            "edges": [],
            "schema_config": {},
            "provenance": {},
            "confidence": "high",
            "tier_tags": [],
            "organization_id": "test-org",
        }

    monkeypatch.setattr(tabular_mod, "tabular_to_ir", fake_tabular_to_ir)
    monkeypatch.setattr(serialize_mod, "serialize_bundle", lambda *a, **kw: None)

    _emit_via_ir(
        mapping=mapping,
        rows=[],
        report=ResolutionReport(),
        organization_id="test-org",
        bundle_dir=tmp_path,
    )

    assert "source_ref" in captured, "_emit_via_ir never called tabular_to_ir"
    assert captured["source_ref"].sha256 == expected_sha, (
        f"expected tree-hash {expected_sha!r}, got {captured['source_ref'].sha256!r}"
    )
