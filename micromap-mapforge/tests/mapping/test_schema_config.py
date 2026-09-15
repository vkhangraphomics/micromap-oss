"""Schema-config loader tests (Theme A1' / #74).

Pins the loader contract that #135 will retarget the mapper/resolver against.
The package-baked default is the microbiome schema (ported from ontology.yaml);
projects can override via an explicit path.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from micromap_mapforge.mapping.schema_config import (
    DEFAULT_SCHEMA_CONFIG_PATH,
    SchemaConfigError,
    builtin_template_path,
    default_schema_config,
    list_builtin_templates,
    load_schema_config,
)


# ---------------------------------------------------------------------------
# Default load
# ---------------------------------------------------------------------------


def test_default_schema_config_path_exists():
    assert DEFAULT_SCHEMA_CONFIG_PATH.is_file(), (
        f"package-baked schema_config.yaml missing at {DEFAULT_SCHEMA_CONFIG_PATH}"
    )


def test_load_schema_config_with_no_arg_returns_packaged_default():
    """load_schema_config() with no path returns the microbiome default."""
    cfg = load_schema_config()
    assert cfg["name"] == "micromap-microbiome"
    assert "Taxon" in cfg["classes"]
    assert "Disease" in cfg["classes"]


def test_default_schema_config_is_cached():
    """default_schema_config() returns the same dict object on every call —
    caches the YAML parse."""
    a = default_schema_config()
    b = default_schema_config()
    assert a is b


# ---------------------------------------------------------------------------
# Structure: must preserve the ported ontology.yaml fidelity
# ---------------------------------------------------------------------------


def test_packaged_default_has_all_ported_node_classes():
    """Every node class from ontology.yaml's 2026-04-27 snapshot must be
    present, so #135 can retarget the mapper without losing labels."""
    cfg = load_schema_config()
    expected_node_classes = {
        "Taxon", "Disease", "Compound", "Drug", "Gene",
        "Protein", "Pathway", "Paper", "BodySite", "Assay", "Study",
    }
    assert expected_node_classes <= set(cfg["classes"].keys())


def test_packaged_default_has_all_ported_relationship_classes():
    cfg = load_schema_config()
    expected_rel_classes = {
        "ASSOCIATED_WITH_DISEASE", "PRODUCES", "PARTICIPATES_IN", "HAS_GENE",
        "MENTIONED_IN", "FOUND_IN", "HAS_PARENT", "TARGETS", "METABOLIZED_BY",
        "TRANSPORTED_BY", "LINKED_TO_DISEASE", "PROCESSES", "TESTED_IN",
        "SAME_AS", "METABOLIZES_DRUG", "AFFECTS_TAXON",
    }
    assert expected_rel_classes <= set(cfg["classes"].keys())


def test_packaged_default_mapforge_extensions_preserve_primary_id():
    """Mapper retargeting in #135 will read x_mapforge.primary_id; verify the
    port preserves the value for representative labels."""
    cfg = load_schema_config()
    assert cfg["classes"]["Taxon"]["x_mapforge"]["primary_id"] == "taxon_id"
    assert cfg["classes"]["Disease"]["x_mapforge"]["primary_id"] == "name_normalized"
    assert cfg["classes"]["Protein"]["x_mapforge"]["primary_id"] == "protein_id"


def test_packaged_default_mapforge_extensions_preserve_common_columns_hint():
    """Mapper retargeting reads x_mapforge.common_columns_hint to map source
    columns to ontology fields. Port must preserve these for the mapper to
    keep working post-#135."""
    cfg = load_schema_config()
    taxon_hints = cfg["classes"]["Taxon"]["x_mapforge"]["common_columns_hint"]
    assert "tax_id" in taxon_hints
    assert "organism" in taxon_hints


def test_packaged_default_relationship_status_preserved():
    """x_mapforge.status (populated|planned) distinguishes loader-emitted
    relationships from declared-but-not-emitted ones. Port preserves it."""
    cfg = load_schema_config()
    assert cfg["classes"]["ASSOCIATED_WITH_DISEASE"]["x_mapforge"]["status"] == "populated"
    assert cfg["classes"]["METABOLIZES_DRUG"]["x_mapforge"]["status"] == "planned"


def test_packaged_default_prefixes_cover_referenced_id_prefixes():
    """Every id_prefix referenced by a class must be defined in top-level
    prefixes — Bioregistry validation in A3' depends on this consistency."""
    cfg = load_schema_config()
    declared = set(cfg["prefixes"].keys())
    used: set[str] = set()
    for cls in cfg["classes"].values():
        used.update(cls.get("id_prefixes", []))
    missing = used - declared
    # 'NCBIGene' is used by Gene but isn't a canonical Bioregistry prefix —
    # 'NCBI Gene' would be the LinkML form. This will surface as A3' work;
    # for now we accept it as a known gap.
    known_gaps = {"NCBIGene"}
    assert missing <= known_gaps, (
        f"id_prefixes referenced but not declared in top-level prefixes: "
        f"{sorted(missing - known_gaps)}"
    )


# ---------------------------------------------------------------------------
# Override path
# ---------------------------------------------------------------------------


def test_load_schema_config_with_explicit_path(tmp_path: Path):
    """Project override: load_schema_config(path) reads from a user-provided
    file, not the package default."""
    custom = tmp_path / "custom.yaml"
    custom.write_text(yaml.safe_dump({
        "name": "custom-domain",
        "version": "1.0.0",
        "prefixes": {"mondo": "https://example.com/x/"},
        "classes": {
            "Widget": {"id_prefixes": ["mondo"], "slots": ["id", "name"]},
        },
    }), encoding="utf-8")
    cfg = load_schema_config(custom)
    assert cfg["name"] == "custom-domain"
    assert "Widget" in cfg["classes"]
    # Default schema NOT mixed in -- override is total.
    assert "Taxon" not in cfg["classes"]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_load_schema_config_rejects_missing_name(tmp_path: Path):
    """Missing 'name' fires before version validation (structural check
    runs first per the loader's ordering)."""
    bad = tmp_path / "no_name.yaml"
    bad.write_text(yaml.safe_dump({
        # name: deliberately omitted
        "version": "1.0.0",  # valid version so the name error fires alone
        "classes": {"Widget": {"slots": ["id"]}},
    }), encoding="utf-8")
    with pytest.raises(SchemaConfigError, match="name"):
        load_schema_config(bad)


def test_load_schema_config_rejects_empty_classes(tmp_path: Path):
    """Empty 'classes' fires before version validation (structural)."""
    bad = tmp_path / "no_classes.yaml"
    bad.write_text(yaml.safe_dump({
        "name": "empty",
        "version": "1.0.0",
        "classes": {},
    }), encoding="utf-8")
    with pytest.raises(SchemaConfigError, match="classes"):
        load_schema_config(bad)


def test_load_schema_config_rejects_non_dict_top_level(tmp_path: Path):
    bad = tmp_path / "list.yaml"
    bad.write_text("- not a mapping\n", encoding="utf-8")
    with pytest.raises(SchemaConfigError, match="mapping"):
        load_schema_config(bad)


def test_load_schema_config_raises_on_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_schema_config(tmp_path / "does-not-exist.yaml")


# ---------------------------------------------------------------------------
# Built-in template discovery (Theme A2' / #74)
# ---------------------------------------------------------------------------


def test_list_builtin_templates_includes_microbiome():
    """The microbiome template is the baseline; must always be present."""
    names = list_builtin_templates()
    assert "microbiome" in names


def test_list_builtin_templates_returns_sorted_list():
    """Stable ordering — CLI 'templates list' relies on this for deterministic
    output and tests assert exact stdout."""
    names = list_builtin_templates()
    assert names == sorted(names)


def test_builtin_template_path_resolves_microbiome():
    p = builtin_template_path("microbiome")
    assert p is not None and p.is_file()
    assert p.name == "microbiome.yaml"


def test_builtin_template_path_returns_none_for_unknown():
    assert builtin_template_path("not-a-real-template") is None


def test_builtin_template_path_is_case_insensitive():
    """Users often type 'Microbiome' or 'MICROBIOME' — accept either."""
    assert builtin_template_path("Microbiome") is not None
    assert builtin_template_path("MICROBIOME") is not None


# ---------------------------------------------------------------------------
# Bare-name resolution for load_schema_config (Theme A2' / #74)
# ---------------------------------------------------------------------------


def test_load_schema_config_accepts_microbiome_bare_name():
    """Passing 'microbiome' (no path) loads the built-in microbiome template."""
    cfg = load_schema_config("microbiome")
    assert cfg["name"] == "micromap-microbiome"
    assert "Taxon" in cfg["classes"]


def test_load_schema_config_bare_name_is_case_insensitive():
    """Bare-name lookup is case-insensitive end-to-end through load_schema_config."""
    cfg = load_schema_config("Microbiome")
    assert cfg["name"] == "micromap-microbiome"


def test_load_schema_config_unknown_name_lists_alternatives():
    """Error message must include every available built-in so users can find
    the one they meant."""
    with pytest.raises(SchemaConfigError) as exc_info:
        load_schema_config("not-a-real-template")
    msg = str(exc_info.value)
    assert "not-a-real-template" in msg
    assert "microbiome" in msg


def test_load_schema_config_local_file_wins_over_builtin_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Resolution order: existing file → built-in name → error. A user with a
    file literally named 'microbiome' (no extension) in CWD gets that file,
    not the built-in. Pins this contract."""
    monkeypatch.chdir(tmp_path)
    local = tmp_path / "microbiome"  # no .yaml extension
    local.write_text(yaml.safe_dump({
        "name": "local-override",
        "version": "1.0.0",
        "classes": {"Widget": {"slots": ["id"]}},
    }), encoding="utf-8")
    cfg = load_schema_config("microbiome")
    assert cfg["name"] == "local-override"
    assert "Widget" in cfg["classes"]
    assert "Taxon" not in cfg["classes"]


def test_load_schema_config_path_object_still_works(tmp_path: Path):
    """Backward compat: a Path argument continues to load as today."""
    custom = tmp_path / "custom.yaml"
    custom.write_text(yaml.safe_dump({
        "name": "custom-domain",
        "version": "1.0.0",
        "classes": {"Widget": {"slots": ["id"]}},
    }), encoding="utf-8")
    cfg = load_schema_config(custom)
    assert cfg["name"] == "custom-domain"


# ---------------------------------------------------------------------------
# Bioregistry validation hook (Theme A3' / #74)
# ---------------------------------------------------------------------------


def test_load_schema_config_runs_bioregistry_validation(tmp_path: Path):
    """A schema with a bogus prefix raises SchemaConfigError at load.
    Pins that the bioregistry_check hook actually fires inside load_schema_config."""
    bad = tmp_path / "bad-prefix.yaml"
    bad.write_text(yaml.safe_dump({
        "name": "bad-prefix-test",
        "version": "1.0.0",
        "prefixes": {"xyzzyqwerty": "https://example/"},
        "classes": {"Widget": {"slots": ["id"]}},
    }), encoding="utf-8")
    with pytest.raises(SchemaConfigError, match="xyzzyqwerty"):
        load_schema_config(bad)


def test_load_schema_config_structural_failure_surfaces_before_prefix_failure(
    tmp_path: Path,
):
    """A schema that is BOTH structurally invalid (missing 'name') AND has a
    bogus prefix surfaces the structural error first. Pins the ordering rule
    in the loader: cheap structural checks before bioregistry lookup."""
    bad = tmp_path / "both-bad.yaml"
    bad.write_text(yaml.safe_dump({
        # 'name' deliberately missing
        "prefixes": {"xyzzyqwerty": "https://example/"},
        "classes": {"Widget": {"slots": ["id"]}},
    }), encoding="utf-8")
    with pytest.raises(SchemaConfigError) as exc_info:
        load_schema_config(bad)
    msg = str(exc_info.value)
    assert "name" in msg
    assert "xyzzyqwerty" not in msg


# ---------------------------------------------------------------------------
# Version validation hook (Theme A4 / #74)
# ---------------------------------------------------------------------------


def test_load_schema_config_runs_version_validation(tmp_path: Path):
    """A schema with a bad version raises SchemaConfigError at load."""
    bad = tmp_path / "bad-version.yaml"
    bad.write_text(yaml.safe_dump({
        "name": "bad-version-test",
        "version": "draft",
        "prefixes": {"mondo": "http://purl.obolibrary.org/obo/MONDO_"},
        "classes": {"Widget": {"slots": ["id"]}},
    }), encoding="utf-8")
    with pytest.raises(SchemaConfigError, match="not valid semver"):
        load_schema_config(bad)


def test_load_schema_config_version_check_runs_before_bioregistry(
    tmp_path: Path,
):
    """A schema with BOTH a bad version AND a bad prefix surfaces the
    version error first. Pins the ordering: structural -> version ->
    Bioregistry."""
    bad = tmp_path / "both-bad.yaml"
    bad.write_text(yaml.safe_dump({
        "name": "both-bad-test",
        "version": "draft",
        "prefixes": {"xyzzyqwerty": "https://example/"},
        "classes": {"Widget": {"slots": ["id"]}},
    }), encoding="utf-8")
    with pytest.raises(SchemaConfigError) as exc_info:
        load_schema_config(bad)
    msg = str(exc_info.value)
    assert "not valid semver" in msg
    assert "xyzzyqwerty" not in msg


def test_load_schema_config_accepts_optional_last_updated_field(tmp_path: Path):
    """last_updated: is opaque to the loader -- present in the loaded dict,
    not validated."""
    good = tmp_path / "with-last-updated.yaml"
    good.write_text(yaml.safe_dump({
        "name": "last-updated-test",
        "version": "1.0.0",
        "last_updated": "2026-06-06",
        "prefixes": {"mondo": "http://purl.obolibrary.org/obo/MONDO_"},
        "classes": {"Widget": {"slots": ["id"]}},
    }), encoding="utf-8")
    cfg = load_schema_config(good)
    assert cfg["last_updated"] == "2026-06-06"
    assert cfg["version"] == "1.0.0"


def test_load_schema_config_rejects_missing_version(tmp_path: Path):
    """version: is now required (A4 promoted it from optional)."""
    bad = tmp_path / "no-version.yaml"
    bad.write_text(yaml.safe_dump({
        "name": "no-version-test",
        # version: deliberately omitted
        "prefixes": {"mondo": "http://purl.obolibrary.org/obo/MONDO_"},
        "classes": {"Widget": {"slots": ["id"]}},
    }), encoding="utf-8")
    with pytest.raises(SchemaConfigError, match="'version' field is required"):
        load_schema_config(bad)
