"""JSON + JSONL inspectors. Pure functions, no network, no LLM."""

import json
from pathlib import Path
from typing import Any

from .types import ColumnProfile, InferredType, SourceFormat, SourceProfile

SAMPLE_CAP = 5


def load_rows_json(path: str | Path) -> list[dict[str, Any]]:
    """Return ALL rows from a JSON-array file (not just the inspector's
    SAMPLE_CAP-truncated profile sample).

    Companion to `inspect_json` — `inspect_json` profiles columns and
    samples for the report, this returns the full row set for the
    downstream resolve/emit pipeline. Closes #123.
    """
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(
            f"{p}: expected top-level array of objects, got {type(data).__name__}"
        )
    for i, obj in enumerate(data):
        if not isinstance(obj, dict):
            raise ValueError(
                f"{p}[{i}]: expected JSON object, got {type(obj).__name__}"
            )
    return data


def load_rows_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Return ALL rows from a JSONL file. Companion to `inspect_jsonl`. (#123)"""
    p = Path(path)
    rows: list[dict[str, Any]] = []
    with p.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{p}:{lineno}: invalid JSON line: {e}") from e
            if not isinstance(obj, dict):
                raise ValueError(
                    f"{p}:{lineno}: expected JSON object, got {type(obj).__name__}"
                )
            rows.append(obj)
    return rows


def inspect_json(path: str | Path) -> SourceProfile:
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(
            f"{p}: expected top-level array of objects, got {type(data).__name__}"
        )
    return _profile_rows(p, "json", data)


def inspect_jsonl(path: str | Path) -> SourceProfile:
    p = Path(path)
    rows: list[dict[str, Any]] = []
    with p.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{p}:{lineno}: invalid JSON line: {e}") from e
            if not isinstance(obj, dict):
                raise ValueError(
                    f"{p}:{lineno}: expected JSON object, got {type(obj).__name__}"
                )
            rows.append(obj)
    return _profile_rows(p, "jsonl", rows)


def _profile_rows(p: Path, format_name: SourceFormat, rows: list[dict[str, Any]]) -> SourceProfile:
    column_names: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                column_names.append(k)

    columns: list[ColumnProfile] = []
    for name in column_names:
        values = [row.get(name) for row in rows]
        non_null = [v for v in values if v is not None]
        null_rate = 1.0 - (len(non_null) / len(rows)) if rows else 0.0
        inferred = _infer_type_py(non_null)
        columns.append(
            ColumnProfile(
                name=name,
                inferred_type=inferred,
                null_rate=round(null_rate, 4),
                distinct_count=len({_hashable(v) for v in non_null}),
                samples=list(non_null[:SAMPLE_CAP]),
            )
        )

    return SourceProfile(
        path=str(p),
        format=format_name,
        row_count_estimate=len(rows),
        columns=columns,
        samples=rows[:SAMPLE_CAP],
    )


def _infer_type_py(values: list[Any]) -> InferredType:
    if not values:
        return "unknown"
    if all(isinstance(v, bool) for v in values):
        return "boolean"
    if all(isinstance(v, int) and not isinstance(v, bool) for v in values):
        return "integer"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
        return "float"
    if all(isinstance(v, str) for v in values):
        return "string"
    if all(isinstance(v, (dict, list)) for v in values):
        return "json"
    return "unknown"


def _hashable(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True)
    return v
