"""PDF table inspector — C2 (#76). Each table is a candidate source."""
import json

import pytest
from click.testing import CliRunner

from micromap_mapforge.cli import main
from micromap_mapforge.inspect.dispatch import inspect as dispatch_inspect
from micromap_mapforge.inspect.dispatch import supported_extensions
from micromap_mapforge.inspect.pdf import extract_tables, inspect_pdf, profile_table


def test_profile_table_infers_types_from_string_cells():
    rows = [
        ["tax_id", "name", "abundance"],
        ["562", "E. coli", "0.31"],
        ["853", "F. prausnitzii", "0.12"],
    ]
    prof = profile_table(rows)
    assert prof.format == "pdf"
    assert prof.path == ""          # path is stamped later by inspect_pdf
    assert prof.row_count_estimate == 2
    by_name = {c.name: c for c in prof.columns}
    assert set(by_name) == {"tax_id", "name", "abundance"}
    assert by_name["tax_id"].inferred_type == "integer"
    assert by_name["abundance"].inferred_type == "float"
    assert by_name["name"].inferred_type == "string"


def test_profile_table_handles_none_cells_and_ragged_rows():
    rows = [
        ["a", "b"],
        ["1", None],        # None cell -> treated as null
        ["2"],              # ragged row -> missing cell is null
    ]
    prof = profile_table(rows)
    by_name = {c.name: c for c in prof.columns}
    assert by_name["a"].inferred_type == "integer"
    assert by_name["b"].null_rate == 1.0


def test_profile_table_replaces_blank_header_cells():
    rows = [["tax_id", None], ["562", "x"]]
    prof = profile_table(rows)
    names = [c.name for c in prof.columns]
    assert names == ["tax_id", "col_1"]   # None header -> col_<index>


def test_profile_table_empty_returns_zero_rows():
    prof = profile_table([])
    assert prof.row_count_estimate == 0
    assert prof.columns == []


def _make_pdf(path, tables):
    """Render born-digital tables to a PDF (GRID style so pdfplumber detects
    ruled cells). ``tables`` is a list of row-lists; a Spacer keeps stacked
    tables from merging into one."""
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle

    doc = SimpleDocTemplate(str(path))
    grid = TableStyle([("GRID", (0, 0), (-1, -1), 0.75, colors.black)])
    elements = []
    for data in tables:
        t = Table(data)
        t.setStyle(grid)
        elements.append(t)
        elements.append(Spacer(1, 30))
    doc.build(elements)
    return path


def test_extract_tables_reads_a_born_digital_table(tmp_path):
    p = _make_pdf(tmp_path / "one.pdf", [
        [["tax_id", "name"], ["562", "E. coli"], ["853", "F. prausnitzii"]],
    ])
    tables = extract_tables(p)
    assert len(tables) == 1
    assert tables[0].rows[0] == ["tax_id", "name"]


def test_inspect_pdf_single_table_no_note(tmp_path):
    p = _make_pdf(tmp_path / "one.pdf", [
        [["tax_id", "abundance"], ["562", "0.31"], ["853", "0.12"]],
    ])
    prof = inspect_pdf(p)
    assert prof.format == "pdf"
    assert prof.path == str(p)
    assert prof.row_count_estimate == 2
    by_name = {c.name: c for c in prof.columns}
    assert by_name["tax_id"].inferred_type == "integer"
    assert by_name["abundance"].inferred_type == "float"
    assert prof.note == ""


def test_inspect_pdf_multi_table_profiles_first_and_notes_the_rest(tmp_path):
    p = _make_pdf(tmp_path / "multi.pdf", [
        [["tax_id"], ["562"]],
        [["doid"], ["DOID:1"]],
    ])
    prof = inspect_pdf(p)
    assert [c.name for c in prof.columns] == ["tax_id"]
    assert "2 tables" in prof.note
    assert "--table" in prof.note


def test_inspect_pdf_table_selector_picks_the_nth(tmp_path):
    p = _make_pdf(tmp_path / "multi.pdf", [
        [["tax_id"], ["562"]],
        [["doid"], ["DOID:1"]],
    ])
    prof = inspect_pdf(p, table=2)
    assert [c.name for c in prof.columns] == ["doid"]


def test_inspect_pdf_unknown_table_index_raises(tmp_path):
    p = _make_pdf(tmp_path / "one.pdf", [[["a"], ["1"]]])
    with pytest.raises(ValueError, match="no table #9"):
        inspect_pdf(p, table=9)


def test_inspect_pdf_no_tables_raises(tmp_path):
    from reportlab.platypus import Paragraph, SimpleDocTemplate
    from reportlab.lib.styles import getSampleStyleSheet
    p = tmp_path / "prose.pdf"
    doc = SimpleDocTemplate(str(p))
    doc.build([Paragraph("Just prose, no tables here.", getSampleStyleSheet()["Normal"])])
    with pytest.raises(ValueError, match="no extractable tables"):
        inspect_pdf(p)


def test_pdf_is_a_supported_extension():
    assert ".pdf" in supported_extensions()


def test_dispatch_routes_pdf_with_table_selector(tmp_path):
    p = _make_pdf(tmp_path / "multi.pdf", [
        [["tax_id"], ["562"]],
        [["doid"], ["DOID:1"]],
    ])
    prof = dispatch_inspect(p, table=2)
    assert [c.name for c in prof.columns] == ["doid"]


def test_dispatch_pdf_in_archive(tmp_path):
    import zipfile
    pdf = _make_pdf(tmp_path / "inner.pdf", [[["tax_id"], ["562"]]])
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.write(pdf, arcname="inner.pdf")
    prof = dispatch_inspect(archive)
    assert prof.format == "pdf"
    assert "inner.pdf" in prof.path


def test_cli_inspect_pdf_writes_report(tmp_path):
    p = _make_pdf(tmp_path / "one.pdf", [[["tax_id", "name"], ["562", "E. coli"]]])
    out = tmp_path / "out"
    res = CliRunner().invoke(main, ["inspect", str(p), "--out", str(out)])
    assert res.exit_code == 0, res.output
    report = json.loads((out / "inspection.json").read_text())
    assert report["format"] == "pdf"
    assert {c["name"] for c in report["columns"]} == {"tax_id", "name"}


def test_cli_inspect_pdf_table_selector(tmp_path):
    p = _make_pdf(tmp_path / "multi.pdf", [
        [["tax_id"], ["562"]],
        [["doid"], ["DOID:1"]],
    ])
    out = tmp_path / "out"
    res = CliRunner().invoke(main, ["inspect", str(p), "--table", "2", "--out", str(out)])
    assert res.exit_code == 0, res.output
    report = json.loads((out / "inspection.json").read_text())
    assert [c["name"] for c in report["columns"]] == ["doid"]
