"""Structured-string detection — D3 (#77 / #76 observation)."""
from micromap_mapforge.inspect.dispatch import inspect
from micromap_mapforge.inspect.structured import detect_structured_string

_GTDB = [
    "d__Bacteria;p__Pseudomonadota;c__Gammaproteobacteria;o__Enterobacterales;"
    "f__Enterobacteriaceae;g__Escherichia;s__Escherichia coli",
    "d__Bacteria;p__Bacillota;c__Clostridia;o__Lachnospirales;"
    "f__Lachnospiraceae;g__Roseburia;s__Roseburia intestinalis",
]


def test_detects_rank_lineage():
    hint = detect_structured_string(_GTDB, "gtdb_taxonomy")
    assert hint == {
        "kind": "rank_lineage", "delimiter": ";",
        "components": ["domain", "phylum", "class", "order", "family", "genus", "species"],
    }


def test_detects_pipe_delimited():
    hint = detect_structured_string(["a|b|c", "d|e|f", "g|h|i"], "codes")
    assert hint == {"kind": "delimited", "delimiter": "|", "parts": 3}


def test_inconsistent_delimited_is_not_structured():
    assert detect_structured_string(["a|b", "c|d|e"], "x") is None


def test_plain_text_is_not_structured():
    assert detect_structured_string(["E. coli", "F. prausnitzii", "B. fragilis"], "name") is None


def test_numeric_is_not_structured():
    assert detect_structured_string(["562", "853", "9606"], "tax_id") is None


def test_too_few_values_is_none():
    assert detect_structured_string(["d__Bacteria;p__X"], "x") is None


def test_inspector_flags_structured_column(tmp_path):
    p = tmp_path / "taxa.tsv"
    p.write_text(
        "name\tgtdb_taxonomy\n"
        f"E. coli\t{_GTDB[0]}\n"
        f"Roseburia\t{_GTDB[1]}\n",
        encoding="utf-8",
    )
    prof = inspect(p)
    by_name = {c.name: c for c in prof.columns}
    assert by_name["gtdb_taxonomy"].structured["kind"] == "rank_lineage"
    assert by_name["name"].structured is None


def test_report_lists_structured_columns(tmp_path):
    from click.testing import CliRunner

    from micromap_mapforge.cli import main

    p = tmp_path / "taxa.tsv"
    p.write_text(f"taxon\tgtdb_taxonomy\nx\t{_GTDB[0]}\ny\t{_GTDB[1]}\n", encoding="utf-8")
    res = CliRunner().invoke(main, ["inspect", str(p), "--out", str(tmp_path / "out")])
    assert res.exit_code == 0
    report = (tmp_path / "out" / "inspection-report.md").read_text(encoding="utf-8")
    assert "Structured columns" in report
    assert "`gtdb_taxonomy`: rank lineage" in report
    assert "genus, species" in report
