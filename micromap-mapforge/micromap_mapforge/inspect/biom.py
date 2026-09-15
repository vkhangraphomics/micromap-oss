"""#286 G2: BIOM feature-table inspector (microbiome).

BIOM (Biological Observation Matrix) is the standard microbiome exchange format.
The 1.0 encoding is documented JSON, parsed here natively (no dependency). The
2.x encoding is HDF5 — recognized and rejected with an actionable convert-to-JSON
step until it is wired behind the ``[omics]`` extra (a follow-up).

A BIOM is a feature x sample count matrix: ``rows`` are observations (OTUs/ASVs/
features), ``columns`` are samples, ``data`` is the matrix. Each observation's
metadata carries the taxonomic lineage. We surface the classic-OTU-table view as
a ``SourceProfile``:

- ``observation_id`` — the feature id
- ``taxonomy``       — the lineage joined into the ``;`` rank form
  (``d__Bacteria;p__...;s__``) so :func:`inspect.structured.detect_structured_string`
  decomposes it into domain/phylum/.../species, mapping straight onto the
  microbiome template's ``Taxon``
- one column per sample — the abundance counts

Pure, offline, no LLM — matches the other inspectors. Everything downstream
(map/resolve/emit) is unchanged since it consumes only ``SourceProfile``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .structured import detect_structured_string
from .types import ColumnProfile, InferredType, SourceProfile, register_format

FORMAT = register_format("biom")

_HDF5_MAGIC = b"\x89HDF\r\n\x1a\n"
_BIOM_MARKER = "Biological Observation Matrix"
SAMPLE_CAP = 5


def inspect_biom(path: str | Path) -> SourceProfile:
    p = Path(path)
    if p.read_bytes()[:8] == _HDF5_MAGIC:
        raise ValueError(
            f"{p}: HDF5-encoded BIOM (2.x) is not parsed natively yet. Convert it "
            f"to BIOM-1.0 JSON first — `biom convert -i {p.name} -o table.json "
            f"--to-json` — then inspect that. (Native HDF5 support is a follow-up "
            f"behind the micromap-mapforge[omics] extra.)"
        )
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(
            f"{p}: not a readable BIOM-1.0 JSON table ({exc}). For HDF5 BIOM, "
            f"convert to JSON with `biom convert --to-json`."
        ) from exc
    if _BIOM_MARKER not in str(doc.get("format", "")):
        raise ValueError(
            f"{p}: not a BIOM table — missing the '{_BIOM_MARKER}' format marker."
        )
    return _profile(p, doc)


def _lineage_str(metadata: Any) -> str:
    """Observation metadata -> a `;`-joined rank lineage string (or "")."""
    if not isinstance(metadata, dict):
        return ""
    tax = metadata.get("taxonomy")
    if isinstance(tax, list):
        return ";".join(str(t) for t in tax)
    if isinstance(tax, str):
        return tax
    return ""


def _values_by_column(doc: dict, n_cols: int) -> dict[int, list[Any]]:
    """Non-zero abundance values bucketed by sample (column) index — from the
    sparse triplets or the dense matrix, in one pass."""
    by_col: dict[int, list[Any]] = {j: [] for j in range(n_cols)}
    for row_idx, col_idx, value in _iter_cells(doc):
        if 0 <= col_idx < n_cols and value != 0:
            by_col[col_idx].append(value)
    return by_col


def _iter_cells(doc: dict):
    """Yield (row_idx, col_idx, value) for both matrix encodings."""
    data = doc.get("data", []) or []
    if doc.get("matrix_type") == "dense":
        for r, row in enumerate(data):
            for c, v in enumerate(row):
                yield r, c, v
    else:  # sparse triplets [row, col, value]
        for entry in data:
            yield entry[0], entry[1], entry[2]


def _infer_numeric(values: list[Any]) -> InferredType:
    if not values:
        return "unknown"
    return "integer" if all(isinstance(v, int) and not isinstance(v, bool)
                            for v in values) else "float"


def _string_column(name: str, values: list[str], n_rows: int,
                    structured: bool = False) -> ColumnProfile:
    non_null = [v for v in values if v != ""]
    return ColumnProfile(
        name=name,
        inferred_type="string",
        null_rate=round(1.0 - (len(non_null) / n_rows), 4) if n_rows else 0.0,
        distinct_count=len(set(non_null)),
        samples=non_null[:SAMPLE_CAP],
        structured=detect_structured_string(non_null, name) if structured else None,
    )


def _sample_column(name: str, present_values: list[Any], n_rows: int) -> ColumnProfile:
    # absent cells are 0 in BIOM; null_rate is the sparsity of the column
    return ColumnProfile(
        name=name,
        inferred_type=_infer_numeric(present_values),
        null_rate=round(1.0 - (len(present_values) / n_rows), 4) if n_rows else 0.0,
        distinct_count=len(set(present_values)),
        samples=present_values[:SAMPLE_CAP],
    )


def _profile(p: Path, doc: dict) -> SourceProfile:
    rows = doc.get("rows", []) or []
    cols = doc.get("columns", []) or []
    shape = doc.get("shape") or [len(rows), len(cols)]
    n_rows, n_cols = int(shape[0]), int(shape[1])

    obs_ids = [str(r.get("id", "")) for r in rows]
    taxonomies = [_lineage_str(r.get("metadata")) for r in rows]
    by_col = _values_by_column(doc, len(cols))

    columns = [
        _string_column("observation_id", obs_ids, n_rows),
        _string_column("taxonomy", taxonomies, n_rows, structured=True),
    ]
    for j, col in enumerate(cols):
        sid = str(col.get("id", f"sample_{j}"))
        columns.append(_sample_column(sid, by_col.get(j, []), n_rows))

    sample_rows = [
        {"observation_id": obs_ids[i], "taxonomy": taxonomies[i]}
        for i in range(min(SAMPLE_CAP, len(rows)))
    ]
    note = (f"BIOM 1.0 JSON {doc.get('type', 'table')}: "
            f"{n_rows} features x {n_cols} samples")
    return SourceProfile(path=str(p), format=FORMAT, row_count_estimate=n_rows,
                         columns=columns, samples=sample_rows, note=note)
