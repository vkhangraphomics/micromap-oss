"""#286 G1: VCF variant-call inspector (genomics).

Plain-text VCF is tab-delimited with ``##`` header lines — parsed here natively
(no dependency). The ``##INFO``/``##FORMAT`` declarations are a typed column
schema, so the fixed columns get their real types (POS integer, QUAL float)
rather than value inference — a better ``SourceProfile`` than CSV can produce.

``.vcf.gz`` (bgzf is gzip-readable) is handled by G0 upstream — it decompresses
to ``.vcf`` and re-dispatches here. Binary ``.bcf`` needs a library; it is
recognized and rejected with an actionable convert step (native BCF is a
follow-up behind the ``[omics]`` extra).

CHROM/POS/ID/REF/ALT map onto the genomics ``Variant``; per-sample genotype
columns onto ``Sample``. Pure, offline, no LLM.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .structured import detect_structured_string
from .types import ColumnProfile, InferredType, SourceProfile, register_format

FORMAT = register_format("vcf")

SAMPLE_CAP = 5
_DATA_CAP = 1000          # rows read for value profiling (row_count counts all)
_BCF_MAGIC = b"BCF"

# The 8 fixed VCF columns carry known types from the spec — use them directly.
_FIXED_TYPES: dict[str, InferredType] = {
    "CHROM": "string", "POS": "integer", "ID": "string", "REF": "string",
    "ALT": "string", "QUAL": "float", "FILTER": "string", "INFO": "string",
    "FORMAT": "string",
}
_MISSING = {"", "."}
_ID_RE = re.compile(r"ID=([^,>]+)")


def inspect_vcf(path: str | Path) -> SourceProfile:
    p = Path(path)
    if p.read_bytes()[:3] == _BCF_MAGIC:
        raise ValueError(
            f"{p}: binary BCF is not parsed natively. Convert it to text VCF first "
            f"— `bcftools view {p.name} -o variants.vcf` — then inspect that. "
            f"(Native BCF is a follow-up behind the micromap-mapforge[omics] extra.)"
        )

    info_fields: list[str] = []
    format_fields: list[str] = []
    fileformat = ""
    header: list[str] | None = None
    n_variants = 0
    rows: list[list[str]] = []

    with p.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.startswith("##"):
                if line.startswith("##fileformat="):
                    fileformat = line.split("=", 1)[1]
                elif line.startswith("##INFO=<"):
                    _record_field(line, info_fields)
                elif line.startswith("##FORMAT=<"):
                    _record_field(line, format_fields)
            elif line.startswith("#CHROM"):
                header = line[1:].split("\t")   # drop the leading '#'
            elif line and header is not None:
                n_variants += 1
                if len(rows) < _DATA_CAP:
                    rows.append(line.split("\t"))

    if header is None:
        raise ValueError(f"{p}: not a VCF — no '#CHROM' header line found.")

    return _profile(p, header, rows, n_variants, fileformat,
                    info_fields, format_fields)


def _record_field(meta_line: str, into: list[str]) -> None:
    m = _ID_RE.search(meta_line)
    if m:
        into.append(m.group(1).strip())


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
        # INFO is a `;`-joined key=value string — flag it as structured so the
        # mapper/reviewer can split it into the declared INFO fields.
        structured=detect_structured_string(non_null, name) if name == "INFO" else None,
    )


def _profile(p: Path, header: list[str], rows: list[list[str]], n_variants: int,
             fileformat: str, info_fields: list[str],
             format_fields: list[str]) -> SourceProfile:
    n_samples = max(0, len(header) - 9)  # 8 fixed + FORMAT, then sample columns
    columns: list[ColumnProfile] = []
    for idx, name in enumerate(header):
        itype = _FIXED_TYPES.get(name, "string")   # sample genotype cols -> string
        values = [r[idx] for r in rows if idx < len(r)]
        columns.append(_column(name, itype, values))

    sample_rows = [dict(zip(header, r, strict=False)) for r in rows[:SAMPLE_CAP]]
    version = fileformat or "VCF"
    note = (
        f"{version}: {n_variants} variants, {n_samples} samples; "
        f"INFO fields [{', '.join(info_fields)}]; "
        f"FORMAT fields [{', '.join(format_fields)}]"
    )
    return SourceProfile(path=str(p), format=FORMAT, row_count_estimate=n_variants,
                         columns=columns, samples=sample_rows, note=note)
