"""#286 G4: mzTab inspector (metabolomics / proteomics).

mzTab is line-oriented: each line starts with a section code. ``MTD`` is
metadata; a header line (``SMH``/``PRH``/``PSH``/``PEH``/``SFH``/``SEH``) declares
the columns for the data rows that follow (``SML``/``PRT``/``PSM``/``PEP``/
``SMF``/``SME``). Parsed natively (tab-delimited text), no dependency.

The inspector profiles the primary data table — small molecules (mzTab-M)
preferred, then proteins/PSMs (mzTab proteomics) — and lists the section
inventory in the note (the established multi-source idiom). Small-molecule columns
(chemical_name/formula/smiles/inchi/database_identifier) map onto the metabolomics
Compound; abundance columns onto Measurement. Pure/offline.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from .types import ColumnProfile, InferredType, SourceProfile, register_format

FORMAT = register_format("mztab")

SAMPLE_CAP = 5
# (header prefix, data prefix), in priority order for the primary table.
_TABLES = [
    ("SMH", "SML"), ("PRH", "PRT"), ("PSH", "PSM"),
    ("PEH", "PEP"), ("SFH", "SMF"), ("SEH", "SME"),
]
_MISSING = {"", "null", "."}


def _looks_int(v: str) -> bool:
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


def _infer(name: str, values: list[str]) -> InferredType:
    non_null = [v for v in values if v not in _MISSING]
    if not non_null:
        return "unknown"
    if name.startswith("abundance"):        # spec: abundance_* are numeric
        return "float"
    if all(_looks_int(v) for v in non_null):
        return "integer"
    if all(_looks_float(v) for v in non_null):
        return "float"
    return "string"


def _cast(v: str, itype: InferredType) -> Any:
    if itype == "integer" and _looks_int(v):
        return int(v)
    if itype == "float" and _looks_float(v):
        return float(v)
    return v


def _column(name: str, values: list[str]) -> ColumnProfile:
    itype = _infer(name, values)
    non_null = [v for v in values if v not in _MISSING]
    return ColumnProfile(
        name=name,
        inferred_type=itype,
        null_rate=round(1.0 - (len(non_null) / len(values)), 4) if values else 0.0,
        distinct_count=len(set(non_null)),
        samples=[_cast(v, itype) for v in non_null[:SAMPLE_CAP]],
    )


def inspect_mztab(path: str | Path, section: str | None = None) -> SourceProfile:
    """Profile an mzTab table.

    ``section`` (#286 G4b) selects which typed table to profile by its data-row
    prefix — ``SML``/``PRT``/``PSM``/``PEP``/``SMF``/``SME`` (case-insensitive),
    following the multi-source idiom (xlsx→--sheet, PDF→--table). Default (None)
    auto-picks the primary table in priority order (small molecules first).
    """
    p = Path(path)
    sections: dict[str, list[list[str]]] = defaultdict(list)
    with p.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            fields = line.split("\t")
            sections[fields[0]].append(fields)

    # Tables actually present (both a header and data section), in priority order.
    present = [(h, d) for h, d in _TABLES if sections.get(h) and sections.get(d)]
    if not present:
        raise ValueError(
            f"{p}: not a readable mzTab — found no header+data table "
            f"(SMH/SML, PRH/PRT, PSH/PSM, ...). Sections seen: "
            f"{sorted(sections)}."
        )

    if section is None:
        hprefix, dprefix = present[0]
    else:
        want = section.strip().upper()
        chosen = next(((h, d) for h, d in present if d == want), None)
        if chosen is None:
            raise ValueError(
                f"{p}: no mzTab section {section!r}. Available sections "
                f"(header+data present): {[d for _h, d in present]}."
            )
        hprefix, dprefix = chosen
    header = sections[hprefix][0][1:]          # drop the section-code column
    data = [row[1:] for row in sections[dprefix]]

    columns = []
    for idx, name in enumerate(header):
        vals = [r[idx] for r in data if idx < len(r)]
        columns.append(_column(name, vals))

    counts = {d: len(sections.get(d, [])) for _h, d in _TABLES if sections.get(d)}
    version = ""
    for row in sections.get("MTD", []):
        if len(row) >= 3 and row[1] == "mzTab-version":
            version = row[2]
    inventory = ", ".join(f"{k}={v}" for k, v in counts.items())
    note = (f"mzTab {version}: primary table {dprefix}; "
            f"sections [{inventory}]").strip()
    sample_rows = [dict(zip(header, r, strict=False)) for r in data[:SAMPLE_CAP]]
    return SourceProfile(path=str(p), format=FORMAT,
                         row_count_estimate=len(data), columns=columns,
                         samples=sample_rows, note=note)
