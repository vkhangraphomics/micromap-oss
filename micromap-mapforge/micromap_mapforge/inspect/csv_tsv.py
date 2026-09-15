"""CSV and TSV inspectors. Pure functions, no network, no LLM."""

import csv as csv_module
from pathlib import Path
from typing import Any

from .structured import detect_structured_string
from .types import ColumnProfile, InferredType, SourceFormat, SourceProfile

SAMPLE_CAP = 5


def inspect_csv(path: str | Path) -> SourceProfile:
    return _inspect_delimited(Path(path), delimiter=",", format_name="csv")


def inspect_tsv(path: str | Path) -> SourceProfile:
    return _inspect_delimited(Path(path), delimiter="\t", format_name="tsv")


def _inspect_delimited(path: Path, delimiter: str, format_name: SourceFormat) -> SourceProfile:
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv_module.DictReader(f, delimiter=delimiter)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    columns: list[ColumnProfile] = []
    for name in fieldnames:
        raw_values = [row.get(name, "") or "" for row in rows]
        non_null = [v for v in raw_values if v != ""]
        null_rate = 1.0 - (len(non_null) / len(rows)) if rows else 0.0
        inferred = _infer_type(non_null)
        samples = _samples(non_null, SAMPLE_CAP, inferred)
        columns.append(
            ColumnProfile(
                name=name,
                inferred_type=inferred,
                null_rate=round(null_rate, 4),
                distinct_count=len(set(non_null)),
                samples=samples,
                structured=detect_structured_string(non_null, name),
            )
        )

    sample_rows = rows[:SAMPLE_CAP]

    return SourceProfile(
        path=str(path),
        format=format_name,
        row_count_estimate=len(rows),
        columns=columns,
        samples=sample_rows,
    )


def _infer_type(values: list[str]) -> InferredType:
    if not values:
        return "unknown"
    if all(_looks_integer(v) for v in values):
        return "integer"
    if all(_looks_float(v) for v in values):
        return "float"
    if all(v.lower() in ("true", "false") for v in values):
        return "boolean"
    return "string"


def _looks_integer(v: str) -> bool:
    try:
        int(v)
        return True
    except ValueError:
        return False


def _looks_float(v: str) -> bool:
    try:
        float(v)
        return True
    except ValueError:
        return False


def _samples(values: list[str], cap: int, inferred: InferredType) -> list[Any]:
    out: list[Any] = []
    for v in values[:cap]:
        if inferred == "integer":
            try:
                out.append(int(v))
                continue
            except ValueError:
                pass
        if inferred == "float":
            try:
                out.append(float(v))
                continue
            except ValueError:
                pass
        if inferred == "boolean":
            out.append(v.lower() in ("true", "1"))
            continue
        out.append(v)
    return out
