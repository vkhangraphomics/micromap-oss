"""#286 G5: GFF3/GTF + BED annotation inspectors (genomics).

Fixed-column tab-delimited text — parsed natively, no dependency.

- GFF3/GTF: 8 fixed columns (seqid/source/type/start/end/score/strand/phase) plus
  a 9th structured attribute column. The attribute keys are EXTRACTED into columns
  (``gene_id``, ``gene_name``, ``transcript_id``, …) so they map straight onto the
  genomics ``Gene``/``Transcript`` — the attribute string IS the schema. GFF3 uses
  ``key=value;`` and GTF uses ``key "value";``.
- BED: positional (chrom/start/end[/name/score/strand/…]).

Pure/offline. `.gff.gz` etc. flow through G0. Everything downstream is unchanged
(consumes only ``SourceProfile``).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .types import ColumnProfile, InferredType, SourceProfile, register_format

register_format("gff3")
register_format("gtf")
register_format("bed")

SAMPLE_CAP = 5
_DATA_CAP = 1000
_MISSING = {"", "."}   # GFF/BED unknown-value sentinels

_GFF_FIXED = ["seqid", "source", "type", "start", "end", "score", "strand", "phase"]
_GFF_TYPES: dict[str, InferredType] = {
    "start": "integer", "end": "integer", "score": "float",
}
# BED positional columns (BED12) + their spec types.
_BED_FIELDS = ["chrom", "start", "end", "name", "score", "strand",
               "thickStart", "thickEnd", "itemRgb", "blockCount",
               "blockSizes", "blockStarts"]
_BED_INT = {"start", "end", "thickStart", "thickEnd", "blockCount"}

_GTF_ATTR = re.compile(r'(\S+)\s+"(.*?)"')


def _cast(value: str, itype: InferredType) -> Any:
    if itype == "integer":
        try:
            return int(value)
        except ValueError:
            return value
    if itype == "float":
        try:
            return float(value)
        except ValueError:
            return value
    return value


def _column(name: str, itype: InferredType, values: list[str]) -> ColumnProfile:
    non_null = [v for v in values if v not in _MISSING]
    return ColumnProfile(
        name=name,
        inferred_type=itype,
        null_rate=round(1.0 - (len(non_null) / len(values)), 4) if values else 0.0,
        distinct_count=len(set(non_null)),
        samples=[_cast(v, itype) for v in non_null[:SAMPLE_CAP]],
    )


def _data_lines(p: Path):
    """Yield non-comment lines; count all, materialize up to the cap."""
    kept: list[str] = []
    total = 0
    with p.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line or line.startswith(("#", "track", "browser")):
                continue
            total += 1
            if len(kept) < _DATA_CAP:
                kept.append(line)
    return kept, total


def _parse_attributes(attr: str, is_gtf: bool) -> dict[str, str]:
    out: dict[str, str] = {}
    if is_gtf:
        for key, value in _GTF_ATTR.findall(attr):
            out[key] = value
    else:
        for part in attr.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def inspect_gff(path: str | Path) -> SourceProfile:
    p = Path(path)
    is_gtf = p.suffix.lower() == ".gtf"
    fmt = "gtf" if is_gtf else "gff3"
    lines, n_features = _data_lines(p)
    rows = [ln.split("\t") for ln in lines]

    # fixed columns
    columns: list[ColumnProfile] = []
    for idx, name in enumerate(_GFF_FIXED):
        vals = [r[idx] for r in rows if idx < len(r)]
        columns.append(_column(name, _GFF_TYPES.get(name, "string"), vals))

    # explode the 9th (attribute) column into per-key columns
    attr_dicts = [_parse_attributes(r[8], is_gtf) for r in rows if len(r) > 8]
    attr_keys: list[str] = []
    for d in attr_dicts:
        for k in d:
            if k not in attr_keys:
                attr_keys.append(k)
    for key in attr_keys:
        vals = [d.get(key, "") for d in attr_dicts]
        columns.append(_column(key, "string", vals))

    types = sorted({r[2] for r in rows if len(r) > 2})
    note = (f"{fmt.upper()}: {n_features} features; types [{', '.join(types)}]; "
            f"attributes [{', '.join(attr_keys)}]")
    sample_rows = [dict(zip(_GFF_FIXED, r, strict=False)) for r in rows[:SAMPLE_CAP]]
    return SourceProfile(path=str(p), format=fmt, row_count_estimate=n_features,
                         columns=columns, samples=sample_rows, note=note)


def inspect_bed(path: str | Path) -> SourceProfile:
    p = Path(path)
    lines, n_features = _data_lines(p)
    rows = [ln.split("\t") for ln in lines]
    n_cols = max((len(r) for r in rows), default=0)

    columns: list[ColumnProfile] = []
    for idx in range(min(n_cols, len(_BED_FIELDS))):
        name = _BED_FIELDS[idx]
        itype: InferredType = ("integer" if name in _BED_INT
                               else "float" if name == "score" else "string")
        vals = [r[idx] for r in rows if idx < len(r)]
        columns.append(_column(name, itype, vals))

    note = f"BED: {n_features} regions, {n_cols} columns"
    sample_rows = [dict(zip(_BED_FIELDS, r, strict=False)) for r in rows[:SAMPLE_CAP]]
    return SourceProfile(path=str(p), format="bed", row_count_estimate=n_features,
                         columns=columns, samples=sample_rows, note=note)
