from pathlib import Path

from micromap_mapforge.inspect.csv_tsv import inspect_csv, inspect_tsv

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_inspect_csv_basic():
    profile = inspect_csv(FIXTURES / "study.csv")
    assert profile.format == "csv"
    assert profile.row_count_estimate == 4
    column_names = [c.name for c in profile.columns]
    assert column_names == ["tax_id", "organism", "condition", "log2fc", "pvalue"]


def test_inspect_csv_type_inference():
    profile = inspect_csv(FIXTURES / "study.csv")
    by_name = {c.name: c for c in profile.columns}
    assert by_name["tax_id"].inferred_type == "integer"
    assert by_name["log2fc"].inferred_type == "float"
    assert by_name["pvalue"].inferred_type == "float"
    assert by_name["organism"].inferred_type == "string"
    assert by_name["condition"].inferred_type == "string"


def test_inspect_csv_null_rate():
    profile = inspect_csv(FIXTURES / "study.csv")
    by_name = {c.name: c for c in profile.columns}
    assert by_name["tax_id"].null_rate == 0.25   # 1 of 4 rows empty


def test_inspect_csv_samples():
    profile = inspect_csv(FIXTURES / "study.csv")
    by_name = {c.name: c for c in profile.columns}
    # Column-level samples are typed per inferred_type
    assert by_name["tax_id"].samples == [562, 1496, 562]
    assert by_name["log2fc"].samples == [1.3, -2.1, 0.8, 0.1]
    assert by_name["organism"].samples[0] == "Escherichia coli"
    # Row-level samples are present
    assert len(profile.samples) > 0
    assert "tax_id" in profile.samples[0]


def test_inspect_tsv_basic():
    profile = inspect_tsv(FIXTURES / "study.tsv")
    assert profile.format == "tsv"
    assert profile.row_count_estimate == 4
    column_names = [c.name for c in profile.columns]
    assert column_names == ["tax_id", "organism", "condition", "log2fc", "pvalue"]


def test_inspect_tsv_tab_delimited():
    profile = inspect_tsv(FIXTURES / "study.tsv")
    by_name = {c.name: c for c in profile.columns}
    # If we mis-detected delimiter, organism values would contain tabs
    assert "Escherichia coli" in by_name["organism"].samples


def test_inspect_csv_distinct_count():
    profile = inspect_csv(FIXTURES / "study.csv")
    by_name = {c.name: c for c in profile.columns}
    # tax_id has values [562, 1496, 562, null] → 2 distinct non-null
    assert by_name["tax_id"].distinct_count == 2
    # organism has values [E.coli, C.diff, E.coli, "Unknown species"] → 3 distinct
    assert by_name["organism"].distinct_count == 3
