"""#286 G3b: 10x Cell Ranger Matrix Market triplet inspector (transcriptomics).

A 10x output is a DIRECTORY: ``matrix.mtx[.gz]`` (Matrix Market sparse counts) +
``features.tsv[.gz]`` (or ``genes.tsv[.gz]`` on v2 — the gene table) +
``barcodes.tsv[.gz]`` (the cells/samples). All text, parsed natively — no
dependency. A bare ``.mtx`` alone has no KG-mappable entities; the gene ids/
symbols live in features.tsv, so this inspector works on the directory.

The features (gene) table is the column surface — gene_id/gene_symbol map onto
the transcriptomics ``Gene``; barcodes are the samples. Handles the gzipped
triplet (10x v3 default). Pure/offline.
"""
from __future__ import annotations

import gzip
from pathlib import Path

from .types import ColumnProfile, SourceProfile, register_format

FORMAT = register_format("10x")
SAMPLE_CAP = 5
# features.tsv columns by position (10x v3; v2 genes.tsv has just the first two).
_FEATURE_COLS = ["gene_id", "gene_symbol", "feature_type"]


def is_10x_dir(path: Path) -> bool:
    """True if the directory holds a 10x matrix (matrix.mtx or matrix.mtx.gz)."""
    return path.is_dir() and any(
        (path / n).exists() for n in ("matrix.mtx", "matrix.mtx.gz"))


def _find(d: Path, *names: str) -> Path | None:
    for n in names:
        for cand in (d / n, d / (n + ".gz")):
            if cand.exists():
                return cand
    return None


def _read_lines(p: Path) -> list[str]:
    opener = gzip.open if p.suffix == ".gz" else open
    with opener(p, "rt", encoding="utf-8", errors="ignore") as fh:
        return [ln.rstrip("\n") for ln in fh if ln.strip()]


def _mtx_shape(p: Path) -> tuple[int, int]:
    """(#rows=genes, #cols=cells) from the Matrix Market dimension line."""
    for ln in _read_lines(p):
        if ln.startswith("%"):
            continue
        parts = ln.split()
        if len(parts) >= 2:
            return int(parts[0]), int(parts[1])
    return 0, 0


def _column(name: str, values: list[str]) -> ColumnProfile:
    non_null = [v for v in values if v != ""]
    return ColumnProfile(
        name=name,
        inferred_type="string",
        null_rate=round(1.0 - (len(non_null) / len(values)), 4) if values else 0.0,
        distinct_count=len(set(non_null)),
        samples=non_null[:SAMPLE_CAP],
    )


def inspect_10x(path: str | Path) -> SourceProfile:
    d = Path(path)
    matrix = _find(d, "matrix.mtx")
    features = _find(d, "features.tsv", "genes.tsv")
    barcodes = _find(d, "barcodes.tsv")
    if matrix is None or features is None:
        raise ValueError(
            f"{d}: not a 10x matrix directory — expected matrix.mtx[.gz] + "
            f"features.tsv[.gz] (or genes.tsv) [+ barcodes.tsv[.gz]]."
        )

    feature_rows = [ln.split("\t") for ln in _read_lines(features)]
    n_genes = len(feature_rows)
    n_cells = len(_read_lines(barcodes)) if barcodes else _mtx_shape(matrix)[1]
    width = max((len(r) for r in feature_rows), default=0)

    columns = []
    for idx in range(min(width, len(_FEATURE_COLS))):
        vals = [r[idx] for r in feature_rows if idx < len(r)]
        columns.append(_column(_FEATURE_COLS[idx], vals))

    note = f"10x Matrix Market: {n_genes} genes x {n_cells} cells"
    sample_rows = [
        dict(zip(_FEATURE_COLS, r, strict=False)) for r in feature_rows[:SAMPLE_CAP]
    ]
    return SourceProfile(path=str(d), format=FORMAT, row_count_estimate=n_genes,
                         columns=columns, samples=sample_rows, note=note)
