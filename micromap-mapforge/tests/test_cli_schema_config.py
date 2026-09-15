"""CLI integration tests for the schema_config flow (Theme A1' / #74).

Pins the end-to-end behavior the foundation slice ships:

1. `mapforge map` copies the package-baked default schema_config.yaml into
   the bundle dir when no override is given.
2. `mapforge map --schema-config <path>` copies the user's file instead.
3. `mapforge emit` reads bundle_dir/schema_config.yaml and embeds it on
   the IR (visible in the produced bundle's mapping.yaml description).
4. Round-trip: the schema_config that goes in matches the schema_config
   that comes out (via the bundle's manifest hash).

#135 (3b-3b) will retarget the mapper at the loaded schema_config; this
PR is foundation-only — the mapper still uses ontology.yaml.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from click.testing import CliRunner

from micromap_mapforge.cli import main
from micromap_mapforge.mapping.schema_config import (
    DEFAULT_SCHEMA_CONFIG_PATH,
    load_schema_config,
)


# ---------------------------------------------------------------------------
# `map` copies schema_config into the bundle
# ---------------------------------------------------------------------------


def _seed_source(tmp_path: Path) -> Path:
    """Minimal TSV the heuristic mapper can produce something from."""
    src = tmp_path / "study.tsv"
    src.write_text(
        "tax_id\torganism\tdisease\n"
        "562\tEscherichia coli\tCrohn's Disease\n",
        encoding="utf-8",
    )
    return src


def test_map_copies_default_schema_config_into_bundle(tmp_path: Path):
    """No --schema-config flag → bundle gets the package-baked default."""
    src = _seed_source(tmp_path)
    out = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(main, ["map", str(src), "--out", str(out), "--mode", "heuristic"])
    assert result.exit_code == 0, result.output

    bundle_schema = out / "schema_config.yaml"
    assert bundle_schema.is_file(), "map must write bundle_dir/schema_config.yaml"

    # Byte-equal to the package-baked default (faithful copy, no rewrite).
    assert bundle_schema.read_bytes() == DEFAULT_SCHEMA_CONFIG_PATH.read_bytes()


def test_map_with_schema_config_flag_uses_override(tmp_path: Path):
    """--schema-config <path> → bundle gets the override, not the default.

    Uses a Taxon class (in mapping.schema.json's strict enum so the mapper
    can emit entities and validation passes) with different x_mapforge
    column hints than the default microbiome shape, so the bundle copy is
    bytewise distinct from the default and exercises slice-2's mapper
    consumption of the override."""
    src = _seed_source(tmp_path)
    out = tmp_path / "out"
    custom = tmp_path / "custom_schema.yaml"
    custom.write_text(yaml.safe_dump({
        "name": "custom-override-test",
        "version": "1.0.0",
        "prefixes": {"NCBITaxon": "http://x/NCBITaxon_"},
        "classes": {
            "Taxon": {
                "id_prefixes": ["NCBITaxon"],
                "slots": ["id", "name", "tax_id"],
                "x_mapforge": {
                    "primary_id": "tax_id",
                    "identifiers": ["tax_id"],
                    "name_field": "name",
                    "common_columns_hint": ["organism"],
                    "normalizer": None,
                },
            },
        },
    }), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, [
        "map", str(src), "--out", str(out), "--mode", "heuristic",
        "--schema-config", str(custom),
    ])
    assert result.exit_code == 0, result.output

    bundle_schema = out / "schema_config.yaml"
    assert bundle_schema.is_file()
    # Byte-equal to the user's override, NOT the default.
    assert bundle_schema.read_bytes() == custom.read_bytes()
    assert bundle_schema.read_bytes() != DEFAULT_SCHEMA_CONFIG_PATH.read_bytes()

    # Round-trip parse must give the override's name + class.
    loaded = load_schema_config(bundle_schema)
    assert loaded["name"] == "custom-override-test"
    assert "Taxon" in loaded["classes"]
    # The mapper-produced mapping.yaml must reflect the override's
    # x_mapforge.identifiers — slice 2 regression guard.
    produced_mapping = yaml.safe_load((out / "mapping.yaml").read_text(encoding="utf-8"))
    taxon = next(e for e in produced_mapping["entities"] if e["label"] == "Taxon")
    # The override declares only 'tax_id' as identifier; mapper must not
    # pull 'ncbi_tax_id' from the default microbiome schema_config.
    assert taxon["match_on"] in {"tax_id", "name"}  # depends on column-match path
    assert "ncbi_tax_id" not in taxon["columns"]


def test_map_preserves_user_authored_schema_config_in_out_dir(tmp_path: Path):
    """If the user dropped a schema_config.yaml into out_dir before `map`,
    we don't overwrite it. Power-user escape hatch."""
    src = _seed_source(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    user_authored = yaml.safe_dump({
        "name": "i-authored-this",
        "prefixes": {},
        "classes": {"Widget": {"slots": ["id"]}},
    })
    (out / "schema_config.yaml").write_text(user_authored, encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, ["map", str(src), "--out", str(out), "--mode", "heuristic"])
    assert result.exit_code == 0, result.output

    # User's authored schema_config survives untouched.
    assert (out / "schema_config.yaml").read_text(encoding="utf-8") == user_authored


def test_map_with_invalid_schema_config_override_fails_fast(tmp_path: Path):
    """A malformed --schema-config surfaces at map time, not later."""
    src = _seed_source(tmp_path)
    out = tmp_path / "out"
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump({
        # missing required 'name' and 'classes'
        "prefixes": {"X": "https://example/x/"},
    }), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, [
        "map", str(src), "--out", str(out), "--mode", "heuristic",
        "--schema-config", str(bad),
    ])
    # SchemaConfigError bubbles out as a non-zero exit; the specific code
    # depends on how Click maps unhandled exceptions, but it must NOT be 0.
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# `emit` reads bundle_dir/schema_config.yaml and embeds it on the IR
# ---------------------------------------------------------------------------


def _seed_emit_ready_bundle(tmp_path: Path) -> Path:
    """A bundle ready for `emit` with a real schema_config in place."""
    out = tmp_path / "out"
    out.mkdir(parents=True)
    src = tmp_path / "s.tsv"
    src.write_text("tax_id\torganism\n562\tEscherichia coli\n", encoding="utf-8")

    (out / "mapping.yaml").write_text(yaml.safe_dump({
        "source": {"name": "s", "format": "tsv", "path": str(src)},
        "entities": [
            {"label": "Taxon", "match_on": "ncbi_tax_id",
             "columns": {"ncbi_tax_id": "tax_id"}, "confidence": "EXTRACTED"},
        ],
        "relationships": [],
    }), encoding="utf-8")
    (out / "routing.yaml").write_text(yaml.safe_dump({
        "destination": "micromap-core",
        "organization_id": "org-test",
        "provenance": {"contributor": "org-test", "submitted_at": "now",
                       "mapping_version": "sha256:x"},
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
    # The schema_config that map would have copied — placed manually for this
    # focused test of the emit path.
    (out / "schema_config.yaml").write_bytes(DEFAULT_SCHEMA_CONFIG_PATH.read_bytes())
    return out


def test_emit_embeds_bundle_schema_config_in_manifest_hashes(tmp_path: Path):
    """After `emit`, manifest.json must hash schema_config.yaml — i.e. the
    bundle is sealed over the schema_config too, not just the cypher/."""
    bundle_dir = _seed_emit_ready_bundle(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["emit", "--bundle", str(bundle_dir)])
    assert result.exit_code == 0, result.output

    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "schema_config.yaml" in manifest["files"], (
        "manifest.json must include schema_config.yaml in its file hashes"
    )

    expected_sha = hashlib.sha256(
        (bundle_dir / "schema_config.yaml").read_bytes()
    ).hexdigest()
    assert manifest["files"]["schema_config.yaml"]["sha256"] == expected_sha


def test_emit_without_bundle_schema_config_still_works(tmp_path: Path):
    """Backward-compat: bundles created before A1' (no schema_config.yaml)
    still emit successfully — tabular_to_ir falls back to its placeholder
    dict."""
    bundle_dir = _seed_emit_ready_bundle(tmp_path)
    (bundle_dir / "schema_config.yaml").unlink()

    runner = CliRunner()
    result = runner.invoke(main, ["emit", "--bundle", str(bundle_dir)])
    assert result.exit_code == 0, result.output
    assert (bundle_dir / "cypher" / "nodes_Taxon.cypher").exists()


# ---------------------------------------------------------------------------
# Manifest carries schema_config_version (Theme A4 / #74)
# ---------------------------------------------------------------------------


def test_map_and_emit_records_schema_config_version_in_manifest(
    tmp_path: Path,
):
    """After 'mapforge map' + 'mapforge emit', the bundle's manifest.json
    carries schema_config_version matching the loaded template's version.

    write_manifest() reads the bundle's schema_config.yaml itself; no
    parameter is threaded from map_cmd. This pins the contract.

    Uses _seed_emit_ready_bundle to create a bundle with the genomics
    schema_config.yaml in place, then runs 'mapforge emit --bundle' to
    exercise the full manifest-write path.
    """
    from micromap_mapforge.mapping.schema_config import builtin_template_path

    # Build a ready-to-emit bundle seeded with the genomics template schema.
    bundle_dir = _seed_emit_ready_bundle(tmp_path)
    # Replace the microbiome schema_config with the genomics one.
    genomics_path = builtin_template_path("genomics")
    assert genomics_path is not None
    (bundle_dir / "schema_config.yaml").write_bytes(genomics_path.read_bytes())

    runner = CliRunner()

    # Pass 5: emit (writes the manifest).
    result = runner.invoke(main, ["emit", "--bundle", str(bundle_dir)])
    assert result.exit_code == 0, result.output

    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    # The contract is "manifest version == the loaded template's version" — assert
    # against the genomics template's ACTUAL declared version, not a literal that
    # changes whenever the template is bumped (#152/#153 version discipline).
    import yaml
    expected_version = str(
        yaml.safe_load(genomics_path.read_text(encoding="utf-8"))["version"]
    )
    assert manifest.get("schema_config_version") == expected_version, (
        f"manifest missing or wrong schema_config_version: {manifest!r} "
        f"(expected {expected_version})"
    )
