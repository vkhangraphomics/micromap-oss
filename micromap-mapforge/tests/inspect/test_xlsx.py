"""Excel (.xlsx) inspector — C1 (#76). Each sheet is a candidate source."""
from pathlib import Path

import openpyxl
import pytest

from micromap_mapforge.inspect.dispatch import inspect
from micromap_mapforge.inspect.xlsx import inspect_xlsx, xlsx_sheet_names


def _make_xlsx(path: Path, sheets: dict[str, list[list]]) -> Path:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # drop the default empty sheet
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    wb.save(path)
    return path


def test_single_sheet_profile(tmp_path):
    p = _make_xlsx(tmp_path / "one.xlsx", {
        "Data": [
            ["tax_id", "name", "abundance"],
            [562, "E. coli", 0.31],
            [853, "F. prausnitzii", 0.12],
        ],
    })
    prof = inspect_xlsx(p)
    assert prof.format == "xlsx"
    assert prof.row_count_estimate == 2
    by_name = {c.name: c for c in prof.columns}
    assert set(by_name) == {"tax_id", "name", "abundance"}
    assert by_name["tax_id"].inferred_type == "integer"
    assert by_name["abundance"].inferred_type == "float"
    assert by_name["name"].inferred_type == "string"
    assert prof.note == ""  # single sheet -> no multi-sheet note


def test_multi_sheet_profiles_first_and_notes_the_rest(tmp_path):
    p = _make_xlsx(tmp_path / "multi.xlsx", {
        "TaxaSheet": [["tax_id"], [562]],
        "DiseaseSheet": [["doid"], ["DOID:1"]],
    })
    prof = inspect_xlsx(p)
    # profiles the first non-empty sheet...
    assert [c.name for c in prof.columns] == ["tax_id"]
    # ...and the note advertises all sheets + how to pick another
    assert "TaxaSheet" in prof.note and "DiseaseSheet" in prof.note
    assert xlsx_sheet_names(p) == ["TaxaSheet", "DiseaseSheet"]


def test_select_specific_sheet(tmp_path):
    p = _make_xlsx(tmp_path / "multi.xlsx", {
        "TaxaSheet": [["tax_id"], [562]],
        "DiseaseSheet": [["doid", "label"], ["DOID:1", "x"]],
    })
    prof = inspect_xlsx(p, sheet="DiseaseSheet")
    assert [c.name for c in prof.columns] == ["doid", "label"]
    assert prof.row_count_estimate == 1


def test_unknown_sheet_raises(tmp_path):
    p = _make_xlsx(tmp_path / "m.xlsx", {"A": [["x"], [1]]})
    with pytest.raises(ValueError):
        inspect_xlsx(p, sheet="NoSuchSheet")


def test_types_and_null_rate(tmp_path):
    p = _make_xlsx(tmp_path / "types.xlsx", {
        "S": [
            ["i", "f", "b", "s", "maybe"],
            [1, 1.5, True, "a", "present"],
            [2, 2.0, False, "b", None],
        ],
    })
    prof = inspect_xlsx(p)
    t = {c.name: c for c in prof.columns}
    assert t["i"].inferred_type == "integer"
    assert t["f"].inferred_type == "float"
    assert t["b"].inferred_type == "boolean"
    assert t["s"].inferred_type == "string"
    assert t["maybe"].null_rate == 0.5  # 1 of 2 rows null


def test_dispatch_routes_xlsx(tmp_path):
    p = _make_xlsx(tmp_path / "d.xlsx", {"S": [["x"], [1]]})
    prof = inspect(p)
    assert prof.format == "xlsx" and [c.name for c in prof.columns] == ["x"]


def test_cli_inspect_reports_note_and_honors_sheet(tmp_path):
    from click.testing import CliRunner

    from micromap_mapforge.cli import main

    p = _make_xlsx(tmp_path / "supp.xlsx", {
        "Taxa": [["tax_id"], [562]],
        "Diseases": [["doid", "label"], ["DOID:1", "x"]],
    })
    runner = CliRunner()

    res = runner.invoke(main, ["inspect", str(p), "--out", str(tmp_path / "out")])
    assert res.exit_code == 0
    report = (tmp_path / "out" / "inspection-report.md").read_text(encoding="utf-8")
    assert "**Note:**" in report and "Diseases" in report  # multi-sheet advertised
    assert "`tax_id`" in report                            # first sheet profiled

    res2 = runner.invoke(main, ["inspect", str(p), "--sheet", "Diseases",
                                "--out", str(tmp_path / "out2")])
    assert res2.exit_code == 0
    report2 = (tmp_path / "out2" / "inspection-report.md").read_text(encoding="utf-8")
    assert "`doid`" in report2 and "`label`" in report2
    assert "**Note:**" not in report2                       # explicit sheet -> no note
