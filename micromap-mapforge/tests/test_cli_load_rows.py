"""#123: ``_load_rows`` must return the full source, not the inspector's
``SAMPLE_CAP``-truncated profile sample.

The fallback branch for non-CSV/TSV formats previously called
``inspect_<format>(path)`` and returned ``profile.samples``, which is
hard-capped at 5 rows. That meant any JSONL / JSON / Parquet / SQL-dump
source with >5 rows silently lost all but the first 5 at resolve / emit
/ submit time — a high-severity data-loss bug surfaced by the #65
HMDB-scale validation pass (217,920-row JSONL → 5 rows reached submit).

These tests pass >5 rows through each format and assert the returned
list has the right length and right content.
"""

import json
from pathlib import Path

import pytest

from micromap_mapforge.cli import _load_rows


def _make_mapping(path: Path, fmt: str) -> dict:
    return {
        "source": {"name": "test", "format": fmt, "path": str(path)},
        "entities": [],
        "relationships": [],
    }


def test_load_rows_csv_returns_all_rows(tmp_path: Path):
    """CSV was the only format that didn't have the bug — regression guard."""
    p = tmp_path / "wide.csv"
    p.write_text(
        "a,b\n" + "\n".join(f"v{i},x{i}" for i in range(20)) + "\n",
        encoding="utf-8",
    )
    rows = _load_rows(_make_mapping(p, "csv"), tmp_path)
    assert len(rows) == 20
    assert rows[0] == {"a": "v0", "b": "x0"}
    assert rows[-1] == {"a": "v19", "b": "x19"}


def test_load_rows_tsv_returns_all_rows(tmp_path: Path):
    p = tmp_path / "wide.tsv"
    p.write_text(
        "a\tb\n" + "\n".join(f"v{i}\tx{i}" for i in range(20)) + "\n",
        encoding="utf-8",
    )
    rows = _load_rows(_make_mapping(p, "tsv"), tmp_path)
    assert len(rows) == 20


def test_load_rows_jsonl_returns_all_rows(tmp_path: Path):
    """#123: 20-row JSONL must return 20 rows, not 5."""
    p = tmp_path / "wide.jsonl"
    p.write_text(
        "\n".join(json.dumps({"a": f"v{i}", "b": i}) for i in range(20)) + "\n",
        encoding="utf-8",
    )
    rows = _load_rows(_make_mapping(p, "jsonl"), tmp_path)
    assert len(rows) == 20, (
        f"expected 20 rows; got {len(rows)} — JSONL still truncating at SAMPLE_CAP"
    )
    assert rows[0]["a"] == "v0"
    assert rows[-1]["a"] == "v19"


def test_load_rows_json_returns_all_rows(tmp_path: Path):
    """#123: 20-row JSON array must return 20 rows."""
    p = tmp_path / "wide.json"
    p.write_text(
        json.dumps([{"a": f"v{i}", "b": i} for i in range(20)]),
        encoding="utf-8",
    )
    rows = _load_rows(_make_mapping(p, "json"), tmp_path)
    assert len(rows) == 20
    assert rows[5]["a"] == "v5"


def test_load_rows_parquet_returns_all_rows(tmp_path: Path):
    """#123: 20-row Parquet must return 20 rows."""
    pyarrow = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    table = pyarrow.table({"a": [f"v{i}" for i in range(20)], "b": list(range(20))})
    p = tmp_path / "wide.parquet"
    pq.write_table(table, p)

    rows = _load_rows(_make_mapping(p, "parquet"), tmp_path)
    assert len(rows) == 20
    assert rows[0]["a"] == "v0"
    assert rows[-1]["b"] == 19


def test_load_rows_sql_dump_returns_all_rows(tmp_path: Path):
    """#123: SQL dump with 20 INSERT rows must return 20 rows."""
    rows_sql = ",\n  ".join(f"('v{i}', {i})" for i in range(20))
    dump = (
        "CREATE TABLE t (a VARCHAR(255), b INT);\n"
        f"INSERT INTO t VALUES\n  {rows_sql};\n"
    )
    p = tmp_path / "wide.sql"
    p.write_text(dump, encoding="utf-8")
    rows = _load_rows(_make_mapping(p, "sql_dump"), tmp_path)
    assert len(rows) == 20, (
        f"expected 20 rows from sql_dump; got {len(rows)} — still truncating"
    )
    assert rows[0]["a"] == "v0"


def test_load_rows_jsonl_with_relative_path_anchored_on_bundle_dir(tmp_path: Path):
    """Compose: #123 fix + #116 relative-path anchoring. JSONL with relative
    source.path must work + return all rows."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "source.jsonl").write_text(
        "\n".join(json.dumps({"a": i}) for i in range(10)) + "\n",
        encoding="utf-8",
    )
    mapping = {
        "source": {"name": "rel", "format": "jsonl", "path": "source.jsonl"},
        "entities": [], "relationships": [],
    }
    rows = _load_rows(mapping, bundle)
    assert len(rows) == 10
