"""Excel (.xlsx / .xlsm) inspector — C1 (#76). Pure, no network, no LLM.

A workbook is potentially many sources: each sheet is a candidate. The standard
dispatch profiles the first non-empty sheet and records the full sheet inventory
in ``SourceProfile.note`` so a multi-sheet workbook is discoverable; pass
``sheet=`` (or ``mapforge inspect --sheet``) to profile a specific one.

Cells carry native Excel types (openpyxl), so type inference uses the Python
types directly rather than re-parsing strings.
"""
from datetime import date, datetime
from pathlib import Path
from typing import Any

import openpyxl

from .structured import detect_structured_string
from .types import ColumnProfile, InferredType, SourceProfile

SAMPLE_CAP = 5


def xlsx_sheet_names(path: str | Path) -> list[str]:
    """Sheet names in workbook order (read-only, doesn't load cell data)."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def _is_empty(ws) -> bool:
    return ws.max_row is None or ws.max_row == 0 or (ws.max_row == 1 and ws.max_column in (None, 0))


def inspect_xlsx(path: str | Path, sheet: str | None = None) -> SourceProfile:
    """Profile one sheet of an .xlsx/.xlsm workbook.

    ``sheet`` selects a worksheet by name; ``None`` picks the first non-empty
    sheet. Raises ``ValueError`` for an unknown sheet name.
    """
    path = Path(path)
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        names = list(wb.sheetnames)
        if sheet is not None:
            if sheet not in names:
                raise ValueError(
                    f"{path}: no sheet named {sheet!r}. Sheets: {names}"
                )
            target = sheet
        else:
            target = next((n for n in names if not _is_empty(wb[n])), names[0] if names else None)
        if target is None:
            return SourceProfile(path=str(path), format="xlsx", row_count_estimate=0, columns=[])

        ws = wb[target]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()

    header: list[str] = []
    data: list[list[Any]] = []
    if rows:
        header = [(str(h) if h is not None else f"col_{i}") for i, h in enumerate(rows[0])]
        data = rows[1:]

    columns: list[ColumnProfile] = []
    for idx, name in enumerate(header):
        cells = [row[idx] if idx < len(row) else None for row in data]
        non_null = [v for v in cells if v is not None and v != ""]
        null_rate = 1.0 - (len(non_null) / len(data)) if data else 0.0
        inferred = _infer_type(non_null)
        columns.append(
            ColumnProfile(
                name=name,
                inferred_type=inferred,
                null_rate=round(null_rate, 4),
                distinct_count=len({str(v) for v in non_null}),
                samples=non_null[:SAMPLE_CAP],
                structured=detect_structured_string(non_null, name),
            )
        )

    sample_rows = [dict(zip(header, row, strict=False)) for row in data[:SAMPLE_CAP]]

    note = ""
    if len(names) > 1 and sheet is None:
        others = [n for n in names if n != target]
        note = (
            f"workbook has {len(names)} sheets {names}; profiled {target!r}. "
            f"Profile another with `--sheet <name>` (e.g. {others[0]!r})."
        )

    return SourceProfile(
        path=str(path),
        format="xlsx",
        row_count_estimate=len(data),
        columns=columns,
        samples=sample_rows,
        note=note,
    )


def _infer_type(values: list[Any]) -> InferredType:
    """Infer a column type from native Excel cell values."""
    if not values:
        return "unknown"
    if all(isinstance(v, bool) for v in values):
        return "boolean"
    # bool is a subclass of int — exclude it from the numeric checks above first.
    non_bool = [v for v in values if not isinstance(v, bool)]
    if non_bool and all(isinstance(v, int) for v in non_bool):
        return "integer"
    if non_bool and all(isinstance(v, (int, float)) for v in non_bool):
        return "float"
    if all(isinstance(v, datetime) for v in values):
        return "datetime"
    if all(isinstance(v, date) for v in values):
        return "date"
    return "string"
