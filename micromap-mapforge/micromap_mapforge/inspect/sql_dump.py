"""Minimal SQL dump inspector. Parses the first CREATE TABLE + INSERTs.

Supported dialect: MySQL/MariaDB-style dumps. Quoted idents may use backticks
or double quotes. One table per dump in v1; additional tables ignored.
"""

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .types import ColumnProfile, InferredType, SourceProfile

SAMPLE_CAP = 5

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+[`\"]?(?P<table>\w+)[`\"]?\s*\((?P<body>.*?)\);",
    re.IGNORECASE | re.DOTALL,
)
_INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+[`\"]?(?P<table>\w+)[`\"]?\s+VALUES\s*\((?P<values>.*?)\);",
    re.IGNORECASE | re.DOTALL,
)


def _parse_dump(path: str | Path) -> tuple[list, list[dict[str, Any]]]:
    """Shared parse path: returns (column_specs, full row list)."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")

    create_match = _CREATE_TABLE_RE.search(text)
    if not create_match:
        raise ValueError(f"{p}: no CREATE TABLE statement found")
    table_name = create_match.group("table")
    column_specs = _parse_column_specs(create_match.group("body"))

    rows: list[dict[str, Any]] = []
    for insert_match in _INSERT_RE.finditer(text):
        if insert_match.group("table") != table_name:
            continue
        captured = insert_match.group("values")
        # Batch INSERT: VALUES (...), (...), (...);  — split into tuples
        for tuple_str in _split_value_tuples(captured):
            row_values = _split_sql_values(tuple_str)
            if len(row_values) != len(column_specs):
                continue
            rows.append(
                {col.name: row_values[i] for i, col in enumerate(column_specs)}
            )
    return column_specs, rows


def load_rows_sql_dump(path: str | Path) -> list[dict[str, Any]]:
    """Return ALL rows from a SQL dump file, typed per the CREATE TABLE.

    Companion to `inspect_sql_dump` — `inspect_sql_dump` profiles columns
    and samples for the report, this returns the full typed row set for
    the downstream resolve/emit pipeline. Closes #123.
    """
    column_specs, rows = _parse_dump(path)
    # Type-coerce the same way inspect_sql_dump does so the row data
    # downstream consumers see matches the inspector's column types.
    inferred_by_col = {c.name: _declared_to_inferred(c.declared_type) for c in column_specs}
    return [
        {name: _coerce(value, inferred_by_col[name]) for name, value in row.items()}
        for row in rows
    ]


def inspect_sql_dump(path: str | Path) -> SourceProfile:
    p = Path(path)
    column_specs, rows = _parse_dump(p)

    columns: list[ColumnProfile] = []
    for i, spec in enumerate(column_specs):
        non_null = [row[spec.name] for row in rows if row[spec.name] is not None]
        null_rate = 1.0 - (len(non_null) / len(rows)) if rows else 0.0
        inferred = _declared_to_inferred(spec.declared_type)
        typed_non_null = [_coerce(v, inferred) for v in non_null]
        columns.append(
            ColumnProfile(
                name=spec.name,
                inferred_type=inferred,
                null_rate=round(null_rate, 4),
                distinct_count=len(set(str(v) for v in typed_non_null)),
                samples=typed_non_null[:SAMPLE_CAP],
            )
        )

    sample_rows = [
        {c.name: _coerce(row[c.name], c.inferred_type) for c in columns}
        for row in rows[:SAMPLE_CAP]
    ]

    return SourceProfile(
        path=str(p),
        format="sql_dump",
        row_count_estimate=len(rows),
        columns=columns,
        samples=sample_rows,
    )


# -- helpers --


@dataclass
class _ColumnSpec:
    name: str
    declared_type: str  # e.g. "INT", "VARCHAR", "FLOAT"


def _parse_column_specs(body: str) -> list[_ColumnSpec]:
    specs: list[_ColumnSpec] = []
    for raw in _split_column_defs(body):
        raw = raw.strip()
        if not raw:
            continue
        # Skip table-level constraints (PRIMARY KEY, KEY, CONSTRAINT, etc.)
        up = raw.upper()
        if up.startswith(("PRIMARY ", "KEY ", "UNIQUE ", "CONSTRAINT ", "FOREIGN ", "INDEX ")):
            continue
        parts = raw.split(None, 2)
        if len(parts) < 2:
            continue
        name = parts[0].strip("`\"")
        declared = parts[1].split("(")[0].upper()
        specs.append(_ColumnSpec(name=name, declared_type=declared))
    return specs


def _split_column_defs(body: str) -> list[str]:
    """Split on commas, respecting parentheses (for e.g. DECIMAL(10,2))."""
    out: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in body:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            out.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        out.append("".join(current))
    return out


def _split_value_tuples(captured: str) -> list[str]:
    """Split a captured VALUES block into individual tuple strings.

    Input: the content between the first '(' after VALUES and the final ');'.
    For single-row: "1, 'a'" (no split needed, returns one element).
    For multi-row:  "1, 'a'), (2, 'b'), (3, 'c')" → ["1, 'a'", "2, 'b'", "3, 'c'"]

    Splits on '), (' patterns that are OUTSIDE single-quoted strings.
    Respects backslash escapes inside quotes.
    """
    parts: list[str] = []
    buf: list[str] = []
    in_quote = False
    i = 0
    n = len(captured)
    while i < n:
        ch = captured[i]
        # Handle escape sequences inside strings
        if ch == "\\" and i + 1 < n:
            buf.append(ch)
            buf.append(captured[i + 1])
            i += 2
            continue
        if ch == "'":
            in_quote = not in_quote
            buf.append(ch)
            i += 1
            continue
        if not in_quote and ch == ")":
            # Look ahead for "), (" tuple separator
            j = i + 1
            while j < n and captured[j] in " \t\r\n":
                j += 1
            if j < n and captured[j] == ",":
                k = j + 1
                while k < n and captured[k] in " \t\r\n":
                    k += 1
                if k < n and captured[k] == "(":
                    # Found tuple separator: close current, skip "), ("
                    parts.append("".join(buf))
                    buf = []
                    i = k + 1
                    continue
        buf.append(ch)
        i += 1
    if buf:
        parts.append("".join(buf))
    return parts


def _split_sql_values(values: str) -> list[Any]:
    """Parse comma-separated SQL VALUES, handling quoted strings and NULL.

    Flattens newlines to spaces first so multi-line VALUES parse as one row.
    """
    flat = " ".join(values.splitlines())
    reader = csv.reader(
        io.StringIO(flat),
        delimiter=",",
        quotechar="'",
        escapechar="\\",
        skipinitialspace=True,
    )
    parsed = next(reader)
    out: list[Any] = []
    for raw in parsed:
        v = raw.strip()
        if v.upper() == "NULL" or v == "":
            out.append(None)
        else:
            out.append(v)
    return out


def _declared_to_inferred(declared: str) -> InferredType:
    d = declared.upper()
    if d in {"INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "MEDIUMINT"}:
        return "integer"
    if d in {"FLOAT", "DOUBLE", "DECIMAL", "NUMERIC", "REAL"}:
        return "float"
    if d in {"BOOLEAN", "BOOL"}:
        return "boolean"
    if d in {"DATE"}:
        return "date"
    if d.startswith("TIMESTAMP") or d == "DATETIME":
        return "datetime"
    if d in {"JSON"}:
        return "json"
    return "string"


def _coerce(v: Any, inferred: InferredType) -> Any:
    if v is None:
        return None
    if inferred == "integer":
        try:
            return int(v)
        except (ValueError, TypeError):
            return v
    if inferred == "float":
        try:
            return float(v)
        except (ValueError, TypeError):
            return v
    return v
