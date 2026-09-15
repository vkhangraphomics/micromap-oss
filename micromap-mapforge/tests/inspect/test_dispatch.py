from pathlib import Path

import pytest

from micromap_mapforge.inspect.dispatch import inspect

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_dispatch_csv():
    assert inspect(FIXTURES / "study.csv").format == "csv"


def test_dispatch_tsv():
    assert inspect(FIXTURES / "study.tsv").format == "tsv"


def test_dispatch_json():
    assert inspect(FIXTURES / "study.json").format == "json"


def test_dispatch_jsonl():
    assert inspect(FIXTURES / "study.jsonl").format == "jsonl"


def test_dispatch_sql_dump():
    assert inspect(FIXTURES / "study_dump.sql").format == "sql_dump"


def test_dispatch_unknown_extension(tmp_path: Path):
    f = tmp_path / "mystery.xyz"
    f.write_text("whatever")
    with pytest.raises(ValueError, match="unsupported file extension"):
        inspect(f)
