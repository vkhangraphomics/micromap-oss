"""#286 G3: AnnData (.h5ad / .loom) inspector (transcriptomics).

AnnData's ``obs``/``var`` ARE sample and feature annotation tables and ``X`` is
the measurement matrix. The inspector profiles ``var`` (the gene/feature table)
as the column surface — its index (gene symbols/ids) plus its annotation columns
(gene_ids, feature_types, …) map onto the transcriptomics ``Gene`` — and records
the obs (sample) / X shape in the note.

``anndata`` is a heavy dependency, imported lazily and declared under the
``[omics]`` extra: ``pip install micromap-mapforge[omics]``. The core install and
the dependency-free path stay light; a call without the extra returns an
actionable install hint. ``anndata`` reads both ``.h5ad`` and ``.loom``.
"""
from __future__ import annotations

from pathlib import Path

from .types import ColumnProfile, SourceProfile, register_format

FORMAT = register_format("anndata")
SAMPLE_CAP = 5


def _str_column(name: str, values: list[str], n_rows: int) -> ColumnProfile:
    non_null = [v for v in values if v not in ("", "nan", "None")]
    return ColumnProfile(
        name=name,
        inferred_type="string",
        null_rate=round(1.0 - (len(non_null) / n_rows), 4) if n_rows else 0.0,
        distinct_count=len(set(non_null)),
        samples=non_null[:SAMPLE_CAP],
    )


def inspect_anndata(path: str | Path) -> SourceProfile:
    p = Path(path)
    try:
        import anndata
    except ImportError as exc:
        raise ValueError(
            f"{p}: reading AnnData (.h5ad) / .loom needs the omics extra — "
            f"`pip install micromap-mapforge[omics]`."
        ) from exc

    if p.suffix.lower() == ".loom":
        adata = anndata.read_loom(str(p))
    else:
        adata = anndata.read_h5ad(str(p), backed="r")   # lazy — don't load X

    var = adata.var
    obs = adata.obs
    columns = [_str_column("var_name", [str(x) for x in var.index.tolist()], adata.n_vars)]
    for col in var.columns:
        columns.append(_str_column(
            str(col), [str(x) for x in var[col].tolist()], adata.n_vars))

    note = (
        f"AnnData: {adata.n_obs} obs (samples) x {adata.n_vars} var (genes); "
        f"obs cols {[str(c) for c in obs.columns]}; "
        f"var cols {[str(c) for c in var.columns]}; "
        f"layers {list(adata.layers.keys())}"
    )
    sample_rows = [
        {"var_name": str(var.index[i]),
         **{str(c): str(var[c].iloc[i]) for c in var.columns}}
        for i in range(min(SAMPLE_CAP, adata.n_vars))
    ]
    return SourceProfile(path=str(p), format=FORMAT,
                         row_count_estimate=int(adata.n_vars),
                         columns=columns, samples=sample_rows, note=note)
