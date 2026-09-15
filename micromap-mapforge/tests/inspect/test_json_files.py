from pathlib import Path

import pytest

from micromap_mapforge.inspect.json_files import inspect_json, inspect_jsonl

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_inspect_json_basic():
    profile = inspect_json(FIXTURES / "study.json")
    assert profile.format == "json"
    assert profile.row_count_estimate == 3
    column_names = sorted(c.name for c in profile.columns)
    assert column_names == ["condition", "log2fc", "organism", "pvalue", "tax_id"]


def test_inspect_json_type_inference():
    profile = inspect_json(FIXTURES / "study.json")
    by_name = {c.name: c for c in profile.columns}
    assert by_name["tax_id"].inferred_type == "integer"
    assert by_name["log2fc"].inferred_type == "float"
    assert by_name["organism"].inferred_type == "string"


def test_inspect_jsonl_basic():
    profile = inspect_jsonl(FIXTURES / "study.jsonl")
    assert profile.format == "jsonl"
    assert profile.row_count_estimate == 3


def test_inspect_json_rejects_single_object():
    path = FIXTURES / "single.json"
    path.write_text('{"tax_id": 1}')
    try:
        with pytest.raises(ValueError, match="expected top-level array"):
            inspect_json(path)
    finally:
        path.unlink()
