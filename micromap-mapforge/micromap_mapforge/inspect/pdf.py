"""PDF table inspector — C2 (#76). Pure profiling, no network, no LLM.

A PDF is potentially many sources: each extracted table is a candidate. The
standard dispatch profiles the first table and records the full table inventory
in ``SourceProfile.note`` so a multi-table PDF is discoverable; pass ``table=``
(or ``mapforge inspect --table``) to profile a specific one (1-based).

Deterministic-first: tables are extracted with pdfplumber (born-digital PDFs).
Cells arrive as strings/None — identical in shape to a CSV row — so type
inference reuses the CSV string-cell helpers rather than reimplementing them.
The Claude-vision fallback for scanned/borderless tables is future work.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pdfplumber

from .csv_tsv import _infer_type, _samples
from .structured import detect_structured_string
from .types import ColumnProfile, SourceProfile

SAMPLE_CAP = 5


def _cell(value: Any) -> str:
    """Normalize a pdfplumber cell (str | None) to a stripped string."""
    return "" if value is None else str(value).strip()


def profile_table(rows: list[list[Any]]) -> SourceProfile:
    """Profile one extracted table (first row = header). Pure: no I/O.

    ``path`` is left empty and ``format`` is ``"pdf"``; ``inspect_pdf`` stamps
    the real path and any multi-table note via ``dataclasses.replace``.
    """
    if not rows:
        return SourceProfile(path="", format="pdf", row_count_estimate=0, columns=[])

    header = [
        (_cell(h) if _cell(h) else f"col_{i}")
        for i, h in enumerate(rows[0])
    ]
    data = rows[1:]

    columns: list[ColumnProfile] = []
    for idx, name in enumerate(header):
        values = [_cell(row[idx]) if idx < len(row) else "" for row in data]
        non_null = [v for v in values if v != ""]
        null_rate = 1.0 - (len(non_null) / len(data)) if data else 0.0
        inferred = _infer_type(non_null)
        columns.append(
            ColumnProfile(
                name=name,
                inferred_type=inferred,
                null_rate=round(null_rate, 4),
                distinct_count=len(set(non_null)),
                samples=_samples(non_null, SAMPLE_CAP, inferred),
                structured=detect_structured_string(non_null, name),
            )
        )

    sample_rows = [
        dict(zip(header, [_cell(c) for c in row], strict=False))
        for row in data[:SAMPLE_CAP]
    ]

    return SourceProfile(
        path="",
        format="pdf",
        row_count_estimate=len(data),
        columns=columns,
        samples=sample_rows,
    )


@dataclass
class PdfTable:
    """One table extracted from a PDF page (1-based page number)."""
    page: int
    rows: list[list[Any]]


def extract_tables(path: str | Path) -> list[PdfTable]:
    """Extract every non-empty table from a born-digital PDF, in page order.

    Pure of MapForge state but does file I/O. Tables with no rows are dropped.
    """
    out: list[PdfTable] = []
    with pdfplumber.open(str(path)) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            for table in page.extract_tables():
                if table and any(any(c is not None for c in row) for row in table):
                    out.append(PdfTable(page=page_no, rows=table))
    return out


def inspect_pdf(path: str | Path, table: int | None = None) -> SourceProfile:
    """Profile one table of a PDF (the first by default).

    ``table`` selects a table by 1-based index across the whole document.
    Raises ``ValueError`` for a PDF with no extractable tables or an
    out-of-range ``table`` index.
    """
    path = Path(path)
    tables = extract_tables(path)
    if not tables:
        raise ValueError(
            f"{path}: no extractable tables found. The PDF may be scanned "
            f"(image-only) or have borderless tables; deterministic extraction "
            f"only handles born-digital ruled/aligned tables."
        )

    selected = 1 if table is None else table
    if not 1 <= selected <= len(tables):
        raise ValueError(
            f"{path}: no table #{selected}. The PDF has {len(tables)} table(s) "
            f"(use --table 1..{len(tables)})."
        )

    chosen = tables[selected - 1]
    prof = profile_table(chosen.rows)

    note = ""
    if len(tables) > 1 and table is None:
        inventory = "; ".join(
            f"#{i} p{t.page} ({len(t.rows)}x{len(t.rows[0]) if t.rows else 0})"
            for i, t in enumerate(tables, start=1)
        )
        note = (
            f"PDF has {len(tables)} tables [{inventory}]; profiled #1. "
            f"Pick another with `--table <n>` (e.g. `--table 2`)."
        )

    return replace(prof, path=str(path), note=note)
