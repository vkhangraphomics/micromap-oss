"""#286 G3: AnnData (.h5ad / .loom) inspector (transcriptomics).

AnnData's `obs`/`var` ARE sample and feature annotation tables; the inspector
profiles `var` (the gene/feature table) as the column surface and records the
obs/X shape in the note. Needs the `[omics]` extra (anndata); the test skips on a
light install and runs where anndata is present (CI installs `.[dev,omics]`).
"""
from pathlib import Path

import pytest

try:
    import anndata
except Exception:
    anndata = None

pytestmark = pytest.mark.skipif(
    anndata is None, reason="anndata not importable (optional [omics] extra)")

from micromap_mapforge.inspect.h5ad import inspect_anndata
from micromap_mapforge.inspect.dispatch import inspect


def _make_h5ad(tmp_path: Path, name: str = "expr.h5ad") -> Path:
    import numpy as np
    import pandas as pd

    var = pd.DataFrame(
        {"gene_ids": ["ENSG1", "ENSG2", "ENSG3"],
         "feature_types": ["Gene Expression"] * 3},
        index=["TP53", "BRCA1", "EGFR"],
    )
    obs = pd.DataFrame({"cell_type": ["T", "B"]}, index=["cell1", "cell2"])
    adata = anndata.AnnData(X=np.array([[1.0, 2, 3], [4, 5, 6]]), obs=obs, var=var)
    p = tmp_path / name
    adata.write_h5ad(p)
    return p


def _col(prof, name):
    return next(c for c in prof.columns if c.name == name)


def test_anndata_profiles_the_var_gene_table(tmp_path: Path):
    prof = inspect_anndata(_make_h5ad(tmp_path))
    assert prof.format == "anndata"
    names = [c.name for c in prof.columns]
    assert "gene_ids" in names and "feature_types" in names   # var columns
    assert "var_name" in names                                # the var index
    assert prof.row_count_estimate == 3                       # 3 genes (var)
    assert "2 obs" in prof.note and "3 var" in prof.note


def test_anndata_var_name_carries_the_gene_symbols(tmp_path: Path):
    prof = inspect_anndata(_make_h5ad(tmp_path))
    assert set(_col(prof, "var_name").samples) >= {"TP53", "BRCA1", "EGFR"}


def test_dispatch_routes_h5ad(tmp_path: Path):
    assert inspect(_make_h5ad(tmp_path)).format == "anndata"


def test_anndata_maps_onto_transcriptomics_gene(tmp_path: Path):
    from micromap_mapforge.mapping.mapper import draft_heuristic_mapping
    from micromap_mapforge.mapping.schema_config import load_schema_config

    prof = inspect_anndata(_make_h5ad(tmp_path))
    mapping = draft_heuristic_mapping(prof, load_schema_config("transcriptomics"))
    labels = [e["label"] for e in mapping.get("entities", [])]
    assert "Gene" in labels
