"""#286 G3b: 10x Matrix Market triplet inspector (transcriptomics).

A 10x Cell Ranger output is a DIRECTORY holding matrix.mtx[.gz] +
features.tsv[.gz] + barcodes.tsv[.gz] — the mappable annotation (gene ids/symbols)
lives in features.tsv, so a bare .mtx alone has no entities. All three are text,
parsed natively. The features (gene) table is the column surface -> Gene; barcodes
are the samples. Handles the gzipped triplet (10x v3 default).
"""
import gzip
from pathlib import Path

from micromap_mapforge.inspect.tenx import inspect_10x
from micromap_mapforge.inspect.dispatch import inspect

_FEATURES = "ENSG1\tTP53\tGene Expression\nENSG2\tBRCA1\tGene Expression\nENSG3\tEGFR\tGene Expression\n"
_BARCODES = "AAACCTGAGAAACCAT-1\nAAACCTGAGAAACCGC-1\n"
_MTX = (
    "%%MatrixMarket matrix coordinate integer general\n"
    "%metadata_json: {}\n"
    "3 2 3\n"          # 3 genes x 2 cells, 3 nonzeros
    "1 1 5\n"
    "2 2 7\n"
    "3 1 2\n"
)


def _make_10x(tmp_path: Path, gz: bool = False) -> Path:
    d = tmp_path / "filtered_feature_bc_matrix"
    d.mkdir()
    files = {"features.tsv": _FEATURES, "barcodes.tsv": _BARCODES, "matrix.mtx": _MTX}
    for name, text in files.items():
        if gz:
            (d / (name + ".gz")).write_bytes(gzip.compress(text.encode()))
        else:
            (d / name).write_text(text, encoding="utf-8")
    return d


def _col(prof, name):
    return next(c for c in prof.columns if c.name == name)


def test_10x_profiles_the_feature_gene_table(tmp_path: Path):
    prof = inspect_10x(_make_10x(tmp_path))
    assert prof.format == "10x"
    names = [c.name for c in prof.columns]
    assert "gene_id" in names and "gene_symbol" in names and "feature_type" in names
    assert prof.row_count_estimate == 3                       # 3 genes
    assert "3 genes" in prof.note and "2 cells" in prof.note


def test_10x_gene_symbols_present(tmp_path: Path):
    prof = inspect_10x(_make_10x(tmp_path))
    assert set(_col(prof, "gene_symbol").samples) >= {"TP53", "BRCA1", "EGFR"}


def test_dispatch_routes_a_10x_directory(tmp_path: Path):
    assert inspect(_make_10x(tmp_path)).format == "10x"


def test_10x_gzipped_triplet(tmp_path: Path):
    # 10x v3 default ships .gz — inspect_10x reads them directly (not via G0)
    assert inspect(_make_10x(tmp_path, gz=True)).format == "10x"


def test_10x_maps_onto_transcriptomics_gene(tmp_path: Path):
    from micromap_mapforge.mapping.mapper import draft_heuristic_mapping
    from micromap_mapforge.mapping.schema_config import load_schema_config

    prof = inspect_10x(_make_10x(tmp_path))
    mapping = draft_heuristic_mapping(prof, load_schema_config("transcriptomics"))
    labels = [e["label"] for e in mapping.get("entities", [])]
    assert "Gene" in labels
