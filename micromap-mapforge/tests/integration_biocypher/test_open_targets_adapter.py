"""Tests for the Open Targets BioCypher adapter (#375).

The upstream `biocypher/open-targets` package pins `requires-python
>=3.10,<3.11`, which cannot coexist with this project's `>=3.11` — so this
is a native adapter reading Open Targets parquet directly via pyarrow
(already a project dependency), not a wrapper around that package. Scope is
intentionally narrow (#375's recommendation): Target/Disease/Molecule nodes
+ a Target-Disease association edge from the small, pre-aggregated
`association_overall_direct` dataset — not the full 40+/50+ node and edge
reference graph, and not the huge raw `evidence/` dataset.

Directory layout under `path` matches Open Targets' real release 25.03+
FTP layout (output/<name>/*.parquet, snake_case & singular since 25.03 —
see #388): target/, disease/, drug_molecule/, association_overall_direct/.
Verified against real downloaded files from
https://ftp.ebi.ac.uk/pub/databases/opentargets/platform/25.03/output/.
"""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from micromap_mapforge.integration.biocypher.adapters.open_targets import (
    MissingColumnsError,
    OpenTargetsAdapter,
)
from micromap_mapforge.integration.biocypher.runner import build_contribution_bundle, run_adapter


def _write_parquet(dir_path: Path, rows: list[dict]) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, dir_path / "part-00000.parquet")


@pytest.fixture
def datasets_dir(tmp_path: Path) -> Path:
    root = tmp_path / "datasets"

    _write_parquet(root / "target", [
        {"id": "ENSG00000157764", "approvedSymbol": "BRAF", "approvedName": "B-Raf proto-oncogene", "biotype": "protein_coding"},
        {"id": "ENSG00000141510", "approvedSymbol": "TP53", "approvedName": "tumor protein p53", "biotype": "protein_coding"},
    ])
    _write_parquet(root / "disease", [
        {"id": "EFO_0000305", "name": "breast carcinoma", "description": "A carcinoma of the breast."},
        {"id": "MONDO_0007254", "name": "melanoma", "description": None},
    ])
    _write_parquet(root / "drug_molecule", [
        {"id": "CHEMBL1201583", "name": "TRASTUZUMAB", "drugType": "Antibody", "isApproved": True},
    ])
    _write_parquet(root / "association_overall_direct", [
        {"targetId": "ENSG00000157764", "diseaseId": "EFO_0000305", "score": 0.85},
        {"targetId": "ENSG00000141510", "diseaseId": "MONDO_0007254", "score": 0.05},  # below default threshold
    ])
    return root


def _node_dict(nodes, node_id):
    for n in nodes:
        if n[0] == node_id:
            return n
    raise AssertionError(f"node {node_id!r} not found among {[n[0] for n in nodes]}")


# ---------------------------------------------------------------------------
# get_nodes()
# ---------------------------------------------------------------------------

def test_target_nodes_are_gene_labeled_with_ensembl_curie(datasets_dir):
    adapter = OpenTargetsAdapter(path=str(datasets_dir))
    nodes = list(adapter.get_nodes())

    node = _node_dict(nodes, "ENSEMBL:ENSG00000157764")
    _node_id, label, props, prov, confidence, tier = node

    assert label == "Gene"
    assert props == {"symbol": "BRAF", "name": "B-Raf proto-oncogene", "biotype": "protein_coding"}
    assert prov == {"source": "open-targets", "dataset": "target"}
    assert confidence == "EXTRACTED"
    assert tier is None


def test_disease_nodes_use_curie_split_on_underscore(datasets_dir):
    adapter = OpenTargetsAdapter(path=str(datasets_dir))
    nodes = list(adapter.get_nodes())

    efo_node = _node_dict(nodes, "EFO:0000305")
    assert efo_node[1] == "Disease"
    assert efo_node[2]["name"] == "breast carcinoma"

    mondo_node = _node_dict(nodes, "MONDO:0007254")
    assert mondo_node[1] == "Disease"
    assert mondo_node[2]["name"] == "melanoma"


def test_molecule_nodes_are_drug_labeled(datasets_dir):
    adapter = OpenTargetsAdapter(path=str(datasets_dir))
    nodes = list(adapter.get_nodes())

    node = _node_dict(nodes, "CHEMBL:CHEMBL1201583")
    assert node[1] == "Drug"
    assert node[2] == {"name": "TRASTUZUMAB", "drug_type": "Antibody", "is_approved": True}


def test_get_nodes_yields_exactly_the_five_rows(datasets_dir):
    adapter = OpenTargetsAdapter(path=str(datasets_dir))
    nodes = list(adapter.get_nodes())
    assert len(nodes) == 5  # 2 targets + 2 diseases + 1 molecule


# ---------------------------------------------------------------------------
# get_edges()
# ---------------------------------------------------------------------------

def test_association_edge_shape(datasets_dir):
    adapter = OpenTargetsAdapter(path=str(datasets_dir))
    edges = list(adapter.get_edges())

    assert len(edges) == 1  # the second row is below the default score threshold
    e_type, from_id, to_id, props, prov, confidence, tier = edges[0]

    assert e_type == "ASSOCIATED_WITH_DISEASE"
    assert from_id == "ENSEMBL:ENSG00000157764"
    assert to_id == "EFO:0000305"
    assert props == {"score": 0.85}
    assert prov == {"source": "open-targets", "dataset": "association_overall_direct"}
    assert confidence == "EXTRACTED"
    assert tier is None


def test_min_association_score_is_configurable(datasets_dir):
    adapter = OpenTargetsAdapter(path=str(datasets_dir), min_association_score=0.0)
    edges = list(adapter.get_edges())
    assert len(edges) == 2


# ---------------------------------------------------------------------------
# Defensive schema checks
# ---------------------------------------------------------------------------

def test_missing_dataset_directory_raises_file_not_found(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    adapter = OpenTargetsAdapter(path=str(empty))
    with pytest.raises(FileNotFoundError, match="target"):
        list(adapter.get_nodes())


def test_missing_required_column_raises_named_error(tmp_path):
    root = tmp_path / "datasets"
    # "targets" dataset missing the required "approvedSymbol" column.
    _write_parquet(root / "target", [{"id": "ENSG00000157764", "biotype": "protein_coding"}])
    _write_parquet(root / "disease", [{"id": "EFO_0000305", "name": "breast carcinoma"}])
    _write_parquet(root / "drug_molecule", [{"id": "CHEMBL1", "name": "X"}])
    _write_parquet(root / "association_overall_direct", [])

    adapter = OpenTargetsAdapter(path=str(root))
    with pytest.raises(MissingColumnsError, match="approvedSymbol"):
        list(adapter.get_nodes())


# ---------------------------------------------------------------------------
# Real interop with the runner (AdapterProtocol compliance)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Memory-bounded reading (#385 -- same class of bug as PrimeKG's original
# OOM, see test_primekg_adapter.py's analogous test): every dataset read
# must stream via pyarrow's Dataset.to_batches(), never
# Dataset.to_table().to_pylist(), which materializes the entire dataset as
# one Arrow Table (and then one Python list) before a single row is yielded.
# Row-count assertions alone can't catch a regression back to full
# materialization -- both approaches produce identical rows. The direct
# proof is behavioral: to_table() must never be called, and rows must
# arrive across multiple batches, not a single bulk read.
# ---------------------------------------------------------------------------

class _SpyDataset:
    """Wraps a real pyarrow Dataset (a C-extension object whose attributes
    are read-only, so it can't be monkeypatched directly) to: (a) raise if
    .to_table() is ever called -- the full-materialization method this fix
    removes -- and (b) record each batch's row count from .to_batches(),
    without touching pyarrow's own (immutable) RecordBatch type."""

    def __init__(self, real_dataset, batches_seen: list[list[int]]):
        self._real = real_dataset
        self._batches_seen = batches_seen

    @property
    def schema(self):
        return self._real.schema

    def to_table(self, *args, **kwargs):
        raise AssertionError(
            "Dataset.to_table() was called -- that fully materializes the dataset "
            "into memory (the exact OOM class this fix removes; see #385/#374). "
            "Use the streaming Dataset.to_batches() instead."
        )

    def to_batches(self, *args, **kwargs):
        counts: list[int] = []
        self._batches_seen.append(counts)
        for batch in self._real.to_batches(*args, **kwargs):
            counts.append(batch.num_rows)
            yield batch


def test_reads_stream_via_to_batches_not_to_table(tmp_path, monkeypatch):
    root = tmp_path / "datasets_large"

    n_rows = 3_000
    _write_parquet(root / "target", [
        {
            "id": f"ENSG{i:011d}",
            "approvedSymbol": f"GENE{i}",
            "approvedName": f"gene {i}",
            "biotype": "protein_coding",
        }
        for i in range(n_rows)
    ])
    _write_parquet(root / "disease", [
        {"id": "EFO_0000305", "name": "breast carcinoma", "description": None},
    ])
    _write_parquet(root / "drug_molecule", [
        {"id": "CHEMBL1", "name": "X", "drugType": "Antibody", "isApproved": True},
    ])
    _write_parquet(root / "association_overall_direct", [
        {"targetId": f"ENSG{i:011d}", "diseaseId": "EFO_0000305", "score": 0.9}
        for i in range(n_rows)
    ])

    from micromap_mapforge.integration.biocypher.adapters import open_targets as open_targets_module

    real_dataset_fn = open_targets_module.ds.dataset
    batches_seen: list[list[int]] = []

    def _spy_dataset(*args, **kwargs):
        return _SpyDataset(real_dataset_fn(*args, **kwargs), batches_seen)

    monkeypatch.setattr(open_targets_module.ds, "dataset", _spy_dataset)

    adapter = OpenTargetsAdapter(path=str(root), batch_size=500)

    nodes = list(adapter.get_nodes())
    edges = list(adapter.get_edges())

    assert len(nodes) == n_rows + 1 + 1  # targets + 1 disease + 1 molecule
    assert len(edges) == n_rows

    # 4 datasets read (target, disease, drug_molecule, association_overall_direct).
    assert len(batches_seen) == 4

    # The real proof: the large datasets (targets, associations) arrived
    # across multiple batches, each a fraction of the file -- genuinely
    # incremental, not a single bulk read dressed up as "streaming".
    targets_batches, _diseases_batches, _molecule_batches, association_batches = batches_seen
    for counts in (targets_batches, association_batches):
        assert len(counts) > 1, "expected multiple batches, not one bulk read"
        assert sum(counts) == n_rows


def test_runs_through_run_adapter_and_build_contribution_bundle(datasets_dir):
    adapter = OpenTargetsAdapter(path=str(datasets_dir))
    offline = run_adapter(adapter)

    assert offline.name == "open-targets"
    assert len(offline.nodes) == 5
    assert len(offline.edges) == 1

    from micromap_mapforge.integration.biocypher.ir import SourceRef

    bundle = build_contribution_bundle(
        offline,
        organization_id="test-org",
        source=SourceRef(kind="dir", path=str(datasets_dir), sha256="0" * 64),
    )
    assert bundle.organization_id == "test-org"
    assert len(bundle.nodes) == 5
    assert len(bundle.edges) == 1
