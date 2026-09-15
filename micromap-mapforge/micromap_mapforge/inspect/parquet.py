"""Parquet inspector using pyarrow."""

from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from .types import ColumnProfile, InferredType, SourceProfile

SAMPLE_CAP = 5


def load_rows_parquet(path: str | Path) -> list[dict[str, Any]]:
    """Return ALL rows from a Parquet file as a list of dicts.

    Companion to `inspect_parquet` — `inspect_parquet` profiles columns
    and samples for the report, this returns the full row set for the
    downstream resolve/emit pipeline. Closes #123.
    """
    p = Path(path)
    return pq.read_table(p).to_pylist()


def inspect_parquet(path: str | Path) -> SourceProfile:
    p = Path(path)
    table = pq.read_table(p)
    total_rows = table.num_rows

    columns: list[ColumnProfile] = []
    for field in table.schema:
        col = table.column(field.name)
        py_values = col.to_pylist()
        non_null = [v for v in py_values if v is not None]
        null_rate = 1.0 - (len(non_null) / total_rows) if total_rows else 0.0
        columns.append(
            ColumnProfile(
                name=field.name,
                inferred_type=_arrow_type_to_inferred(field.type),
                null_rate=round(null_rate, 4),
                distinct_count=len(set(_hashable(v) for v in non_null)),
                samples=list(non_null[:SAMPLE_CAP]),
            )
        )

    sample_rows: list[dict[str, Any]] = table.slice(0, SAMPLE_CAP).to_pylist()

    return SourceProfile(
        path=str(p),
        format="parquet",
        row_count_estimate=total_rows,
        columns=columns,
        samples=sample_rows,
    )


def _arrow_type_to_inferred(t) -> InferredType:
    s = str(t)
    if s.startswith("int") or s.startswith("uint"):
        return "integer"
    if s.startswith("float") or s.startswith("double") or s.startswith("decimal"):
        return "float"
    if s == "bool":
        return "boolean"
    if s.startswith("timestamp"):
        return "datetime"
    if s == "date32[day]" or s == "date64[ms]":
        return "date"
    if s == "string" or s.startswith("large_string"):
        return "string"
    if s.startswith("struct") or s.startswith("list") or s.startswith("map"):
        return "json"
    return "unknown"


def _hashable(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        import json as _json
        return _json.dumps(v, sort_keys=True, default=str)
    return v
