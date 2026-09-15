"""#286 G4: mzTab inspector (metabolomics / proteomics).

mzTab is line-oriented: every line is prefixed by a section code. MTD is
metadata; a header line (SMH/PRH/PSH/PEH) declares the columns for the data rows
that follow (SML/PRT/PSM/PEP). The inspector profiles the primary data table
(small molecules preferred, then proteins/PSMs) and lists the section inventory
in the note — so an mzTab-M small-molecule table maps onto the metabolomics
Compound, and abundance columns onto Measurement.
"""
from pathlib import Path

from micromap_mapforge.inspect.mztab import inspect_mztab
from micromap_mapforge.inspect.dispatch import inspect

_MZTAB_M = (
    "MTD\tmzTab-version\t2.0.0-M\n"
    "MTD\tmzTab-ID\tMTBLS123\n"
    "SMH\tSML_ID\tSMF_ID_REFS\tdatabase_identifier\tchemical_formula\tsmiles\tinchi\tchemical_name\tabundance_assay[1]\tabundance_assay[2]\n"
    "SML\t1\t1\tCHEBI:16236\tC2H6O\tCCO\tInChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3\tethanol\t1000.5\t2000.1\n"
    "SML\t2\t2\tHMDB:HMDB0000122\tC6H12O6\tOC[C@@H]\tInChI=1S/C6H12O6\tglucose\t500.0\t750.0\n"
)


def _write(tmp_path: Path, text: str = _MZTAB_M, name: str = "study.mztab") -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def _col(prof, name):
    return next(c for c in prof.columns if c.name == name)


def test_mztab_profiles_the_small_molecule_table(tmp_path: Path):
    prof = inspect_mztab(_write(tmp_path))
    assert prof.format == "mztab"
    names = [c.name for c in prof.columns]
    for c in ["database_identifier", "chemical_formula", "smiles", "inchi", "chemical_name"]:
        assert c in names
    assert "abundance_assay[1]" in names          # abundance columns preserved
    assert prof.row_count_estimate == 2           # 2 small molecules


def test_mztab_abundance_columns_are_numeric(tmp_path: Path):
    prof = inspect_mztab(_write(tmp_path))
    ab = _col(prof, "abundance_assay[1]")
    assert ab.inferred_type == "float"
    assert 1000.5 in ab.samples


def test_mztab_note_lists_sections(tmp_path: Path):
    prof = inspect_mztab(_write(tmp_path))
    assert "SML" in prof.note and "2" in prof.note   # section inventory + counts


def test_dispatch_routes_dot_mztab(tmp_path: Path):
    assert inspect(_write(tmp_path)).format == "mztab"


def test_mztab_maps_onto_metabolomics_compound(tmp_path: Path):
    from micromap_mapforge.mapping.mapper import draft_heuristic_mapping
    from micromap_mapforge.mapping.schema_config import load_schema_config

    prof = inspect_mztab(_write(tmp_path))
    mapping = draft_heuristic_mapping(prof, load_schema_config("metabolomics"))
    labels = [e["label"] for e in mapping.get("entities", [])]
    assert "Compound" in labels


# -- #286 G4b: --section selector (choose which typed table to profile) --

# A file carrying BOTH a small-molecule table (SML, 2 rows — the auto-pick
# primary) AND a protein table (PRT, 3 rows).
_MZTAB_MULTI = (
    "MTD\tmzTab-version\t2.0.0-M\n"
    "SMH\tSML_ID\tdatabase_identifier\tchemical_name\tabundance_assay[1]\n"
    "SML\t1\tCHEBI:16236\tethanol\t1000.5\n"
    "SML\t2\tHMDB:HMDB0000122\tglucose\t500.0\n"
    "PRH\taccession\tdescription\tbest_search_engine_score[1]\n"
    "PRT\tP12345\tAlpha subunit\t50.0\n"
    "PRT\tP67890\tBeta subunit\t42.0\n"
    "PRT\tP11111\tGamma subunit\t30.0\n"
)


def test_mztab_section_selects_a_non_primary_table(tmp_path: Path):
    # Default auto-picks SML (2 rows); --section PRT profiles the protein table.
    p = _write(tmp_path, _MZTAB_MULTI)
    prof = inspect_mztab(p, section="PRT")
    names = [c.name for c in prof.columns]
    assert names == ["accession", "description", "best_search_engine_score[1]"]
    assert prof.row_count_estimate == 3          # 3 proteins, not the 2 SMLs


def test_mztab_section_is_case_insensitive(tmp_path: Path):
    p = _write(tmp_path, _MZTAB_MULTI)
    assert inspect_mztab(p, section="prt").row_count_estimate == 3


def test_mztab_default_still_auto_picks_the_primary(tmp_path: Path):
    p = _write(tmp_path, _MZTAB_MULTI)
    prof = inspect_mztab(p)                       # no section
    assert prof.row_count_estimate == 2          # SML wins the priority order
    assert "database_identifier" in [c.name for c in prof.columns]


def test_mztab_unknown_section_raises_listing_available(tmp_path: Path):
    import pytest
    p = _write(tmp_path, _MZTAB_MULTI)
    with pytest.raises(ValueError, match="(?i)section") as ei:
        inspect_mztab(p, section="PSM")          # not present in this file
    msg = str(ei.value)
    assert "SML" in msg and "PRT" in msg         # names the tables that ARE present


def test_dispatch_threads_section_to_mztab(tmp_path: Path):
    p = _write(tmp_path, _MZTAB_MULTI)
    prof = inspect(p, section="PRT")
    assert prof.format == "mztab"
    assert prof.row_count_estimate == 3


def test_cli_inspect_mztab_section_selector(tmp_path: Path):
    import json

    from click.testing import CliRunner

    from micromap_mapforge.cli import main

    p = _write(tmp_path, _MZTAB_MULTI)
    out = tmp_path / "out"
    res = CliRunner().invoke(main, ["inspect", str(p), "--section", "PRT", "--out", str(out)])
    assert res.exit_code == 0, res.output
    report = json.loads((out / "inspection.json").read_text())
    assert report["format"] == "mztab"
    assert [c["name"] for c in report["columns"]] == [
        "accession", "description", "best_search_engine_score[1]"]
