"""Curated KG source catalog — C6 (#76)."""
import pytest

from micromap_mapforge.catalog import (
    ADAPTER_STATUS,
    COMMERCIAL_USE,
    CatalogError,
    catalog_caveat,
    get_source,
    list_source_ids,
    load_catalog,
)


def test_shipped_catalog_is_valid_and_sorted():
    sources = load_catalog()
    assert len(sources) >= 15
    assert [s.id for s in sources] == sorted(s.id for s in sources)  # sorted by id
    for s in sources:
        assert s.commercial_use in COMMERCIAL_USE
        assert s.adapter_status in ADAPTER_STATUS
        assert s.last_validated and s.license and s.biolink_categories
    ids = list_source_ids()
    assert "reactome" in ids and "drugbank" in ids and "open-targets" in ids


def test_caveat_present():
    assert "verify" in catalog_caveat().lower()


def test_get_source():
    s = get_source("drugbank")
    assert s is not None and s.commercial_use == "prohibited"  # paid commercial license
    assert get_source("does-not-exist") is None


def test_bad_enum_raises(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        "sources:\n"
        "  - id: x\n    name: X\n    homepage: h\n    description: d\n"
        "    license: MIT\n    commercial_use: maybe\n    license_notes: n\n"
        "    adapter_status: upstream\n    adapter_location: ''\n"
        "    biolink_categories: [Gene]\n    recommended_for: [a]\n"
        "    est_size: s\n    last_validated: '2026-01-01'\n",
        encoding="utf-8",
    )
    with pytest.raises(CatalogError):
        load_catalog(p)


def test_duplicate_ids_raise(tmp_path):
    entry = (
        "  - id: dup\n    name: X\n    homepage: h\n    description: d\n"
        "    license: MIT\n    commercial_use: allowed\n    license_notes: n\n"
        "    adapter_status: unknown\n    adapter_location: ''\n"
        "    biolink_categories: [Gene]\n    recommended_for: [a]\n"
        "    est_size: s\n    last_validated: '2026-01-01'\n"
    )
    p = tmp_path / "dup.yaml"
    p.write_text("sources:\n" + entry + entry, encoding="utf-8")
    with pytest.raises(CatalogError):
        load_catalog(p)


def test_missing_field_raises(tmp_path):
    p = tmp_path / "missing.yaml"
    p.write_text("sources:\n  - id: x\n    name: X\n", encoding="utf-8")
    with pytest.raises(CatalogError):
        load_catalog(p)


# --- CLI ---

def _run(args):
    from click.testing import CliRunner

    from micromap_mapforge.cli import main
    return CliRunner().invoke(main, args)


def test_cli_sources_list():
    res = _run(["sources", "list"])
    assert res.exit_code == 0
    assert "reactome" in res.output and "CC-BY-4.0" in res.output


def test_cli_sources_list_commercial_ok_excludes_prohibited():
    res = _run(["sources", "list", "--commercial-ok"])
    assert res.exit_code == 0
    assert "reactome" in res.output      # allowed
    assert "drugbank" not in res.output  # prohibited -> filtered out


def test_cli_sources_list_adapter_filter():
    res = _run(["sources", "list", "--adapter", "needs_contribution"])
    assert res.exit_code == 0
    assert "reactome" in res.output       # needs_contribution
    assert "open-targets" not in res.output  # upstream -> filtered out


def test_cli_sources_show():
    res = _run(["sources", "show", "drugbank"])
    assert res.exit_code == 0
    assert "DrugBank" in res.output
    assert "commercial_use: prohibited" in res.output
    assert "NOTE:" in res.output  # caveat printed


def test_cli_sources_show_unknown_exits_2():
    res = _run(["sources", "show", "nope"])
    assert res.exit_code == 2
    assert "not in the catalog" in res.output
