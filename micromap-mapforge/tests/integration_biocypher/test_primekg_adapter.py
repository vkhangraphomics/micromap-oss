"""Tests for the PrimeKG BioCypher adapter (#374).

`path` is expected to hold the two real Harvard Dataverse filenames
unmodified: `nodes.tab` (tab-delimited: node_index, node_id, node_type,
node_name, node_source) and `edges.csv` (comma-delimited: relation,
display_relation, x_index, y_index — no embedded endpoint types, so edges
are resolved against the node index).

Node labels map PrimeKG's own node_type onto this project's canonical names
where one exists (gene/protein->Gene, disease->Disease, drug->Drug,
pathway->Pathway); everything else is PascalCased from PrimeKG's own type
name. `exposure` nodes (CTD-sourced — the same commercial-use-restricted
source flagged during the OptimusKG evaluation) are excluded by default,
configurable via `exclude_node_types`; edges referencing an excluded node
are dropped too, not left dangling.

Edge types pass through PrimeKG's own `relation` value (uppercased) rather
than being remapped onto this project's canonical relationship vocabulary —
faithful pass-through beats a hand-built 29-entry mapping table that
couldn't be fully verified against live data this session.
"""

from pathlib import Path

import pyarrow as pa
import pyarrow.csv as pa_csv
import pytest

from micromap_mapforge.integration.biocypher.adapters.primekg import (
    MissingColumnsError,
    PrimeKGAdapter,
)
from micromap_mapforge.integration.biocypher.runner import build_contribution_bundle, run_adapter


def _write_nodes_tab(path: Path, rows: list[dict]) -> None:
    table = pa.Table.from_pylist(rows)
    with open(path, "wb") as f:
        pa_csv.write_csv(table, f, write_options=pa_csv.WriteOptions(delimiter="\t"))


def _write_edges_csv(path: Path, rows: list[dict]) -> None:
    table = pa.Table.from_pylist(rows)
    pa_csv.write_csv(table, path)


@pytest.fixture
def datasets_dir(tmp_path: Path) -> Path:
    root = tmp_path / "primekg"
    root.mkdir()

    _write_nodes_tab(root / "nodes.tab", [
        {"node_index": 0, "node_id": "9796", "node_type": "gene/protein", "node_name": "PHYHIP", "node_source": "NCBI"},
        {"node_index": 1, "node_id": "7189", "node_type": "disease", "node_name": "osteogenesis imperfecta", "node_source": "MONDO"},
        {"node_index": 2, "node_id": "DB09130", "node_type": "drug", "node_name": "Copper", "node_source": "DrugBank"},
        {"node_index": 3, "node_id": "R-HSA-109581", "node_type": "pathway", "node_name": "Apoptosis", "node_source": "REACTOME"},
        {"node_index": 4, "node_id": "C092102", "node_type": "exposure", "node_name": "1-hydroxyphenanthrene", "node_source": "CTD"},
    ])
    _write_edges_csv(root / "edges.csv", [
        {"relation": "disease_protein", "display_relation": "associated with", "x_index": 1, "y_index": 0},
        {"relation": "protein_protein", "display_relation": "ppi", "x_index": 0, "y_index": 0},
        # References the excluded exposure node (index 4) — must be dropped, not left dangling.
        {"relation": "exposure_protein", "display_relation": "interacts with", "x_index": 4, "y_index": 0},
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

def test_canonical_node_types_map_onto_project_labels(datasets_dir):
    adapter = PrimeKGAdapter(path=str(datasets_dir))
    nodes = list(adapter.get_nodes())

    gene = _node_dict(nodes, "NCBI:9796")
    assert gene[1] == "Gene"
    assert gene[2] == {"name": "PHYHIP"}

    disease = _node_dict(nodes, "MONDO:7189")
    assert disease[1] == "Disease"

    drug = _node_dict(nodes, "DrugBank:DB09130")
    assert drug[1] == "Drug"

    pathway = _node_dict(nodes, "REACTOME:R-HSA-109581")
    assert pathway[1] == "Pathway"


def test_node_provenance_and_confidence(datasets_dir):
    adapter = PrimeKGAdapter(path=str(datasets_dir))
    nodes = list(adapter.get_nodes())

    _id, _label, _props, prov, confidence, tier = _node_dict(nodes, "NCBI:9796")
    assert prov == {"source": "primekg", "node_source": "NCBI"}
    assert confidence == "EXTRACTED"
    assert tier is None


def test_exposure_nodes_excluded_by_default(datasets_dir):
    adapter = PrimeKGAdapter(path=str(datasets_dir))
    nodes = list(adapter.get_nodes())
    assert "CTD:C092102" not in [n[0] for n in nodes]
    assert len(nodes) == 4  # 5 rows minus the excluded exposure node


def test_exclude_node_types_is_configurable(datasets_dir):
    adapter = PrimeKGAdapter(path=str(datasets_dir), exclude_node_types=frozenset())
    nodes = list(adapter.get_nodes())
    assert "CTD:C092102" in [n[0] for n in nodes]
    assert len(nodes) == 5


# ---------------------------------------------------------------------------
# get_edges()
# ---------------------------------------------------------------------------

def test_edge_relation_type_is_uppercased_and_endpoints_resolved(datasets_dir):
    adapter = PrimeKGAdapter(path=str(datasets_dir))
    edges = list(adapter.get_edges())

    disease_protein = next(e for e in edges if e[0] == "DISEASE_PROTEIN")
    _e_type, from_id, to_id, props, prov, confidence, tier = disease_protein

    assert from_id == "MONDO:7189"
    assert to_id == "NCBI:9796"
    assert props == {"display_relation": "associated with"}
    assert prov == {"source": "primekg"}
    assert confidence == "EXTRACTED"
    assert tier is None


def test_edges_referencing_excluded_nodes_are_dropped(datasets_dir):
    adapter = PrimeKGAdapter(path=str(datasets_dir))
    edges = list(adapter.get_edges())

    assert "EXPOSURE_PROTEIN" not in [e[0] for e in edges]
    assert len(edges) == 2  # 3 rows minus the one touching the excluded exposure node


def test_get_edges_works_without_get_nodes_called_first(datasets_dir):
    """The node-index lookup edges depend on must not require get_nodes() to
    have run first — run_adapter() happens to call them in that order, but
    nothing should silently depend on it."""
    adapter = PrimeKGAdapter(path=str(datasets_dir))
    edges = list(adapter.get_edges())
    assert len(edges) == 2


# ---------------------------------------------------------------------------
# Defensive schema checks
# ---------------------------------------------------------------------------

def test_missing_nodes_file_raises_file_not_found(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    adapter = PrimeKGAdapter(path=str(empty))
    with pytest.raises(FileNotFoundError, match="nodes.tab"):
        list(adapter.get_nodes())


def test_missing_required_column_raises_named_error(tmp_path):
    root = tmp_path / "primekg"
    root.mkdir()
    # nodes.tab missing the required "node_source" column.
    _write_nodes_tab(root / "nodes.tab", [
        {"node_index": 0, "node_id": "9796", "node_type": "gene/protein", "node_name": "PHYHIP"},
    ])
    _write_edges_csv(root / "edges.csv", [])

    adapter = PrimeKGAdapter(path=str(root))
    with pytest.raises(MissingColumnsError, match="node_source"):
        list(adapter.get_nodes())


# ---------------------------------------------------------------------------
# Memory-bounded reading (real-PrimeKG regression -- see the plan's task-9
# report): _read_nodes()/_read_edges() must stream via pyarrow's genuinely
# incremental `open_csv()` reader, never `read_csv().to_pylist()`, which
# materializes the entire file as Python dicts in one shot. Row-count
# assertions alone (the tests above) can't catch a regression back to full
# materialization -- both approaches produce identical rows. The direct
# proof is behavioral: read_csv() must never be called, and each batch
# handed to to_pylist() must be a fraction of the file, not the whole thing.
# ---------------------------------------------------------------------------

class _CountingReader:
    """Wraps a pyarrow CSVStreamingReader to record each batch's row count,
    without touching pyarrow's (immutable, C-extension) RecordBatch type."""

    def __init__(self, reader, batch_row_counts: list[int]):
        self._reader = reader
        self._batch_row_counts = batch_row_counts

    @property
    def schema(self):
        return self._reader.schema

    def __iter__(self):
        return self

    def __next__(self):
        batch = next(self._reader)
        self._batch_row_counts.append(batch.num_rows)
        return batch


def test_read_nodes_and_edges_stream_via_open_csv_not_read_csv(tmp_path, monkeypatch):
    root = tmp_path / "primekg_large"
    root.mkdir()

    n_rows = 60_000
    _write_nodes_tab(root / "nodes.tab", [
        {
            "node_index": i,
            "node_id": str(i),
            "node_type": "gene/protein",
            "node_name": f"GENE{i}",
            "node_source": "NCBI",
        }
        for i in range(n_rows)
    ])
    _write_edges_csv(root / "edges.csv", [
        {
            "relation": "protein_protein",
            "display_relation": "ppi",
            "x_index": i,
            "y_index": (i + 1) % n_rows,
        }
        for i in range(n_rows)
    ])

    from micromap_mapforge.integration.biocypher.adapters import primekg as primekg_module

    def _boom(*args, **kwargs):
        raise AssertionError(
            "pa_csv.read_csv() was called -- that fully materializes the CSV "
            "into memory (the exact OOM this fix removes). Use the streaming "
            "pa_csv.open_csv() reader instead."
        )

    monkeypatch.setattr(primekg_module.pa_csv, "read_csv", _boom)

    real_open_csv = primekg_module.pa_csv.open_csv
    calls: list[list[int]] = []

    def _spy_open_csv(*args, **kwargs):
        batch_row_counts: list[int] = []
        calls.append(batch_row_counts)
        return _CountingReader(real_open_csv(*args, **kwargs), batch_row_counts)

    monkeypatch.setattr(primekg_module.pa_csv, "open_csv", _spy_open_csv)

    adapter = PrimeKGAdapter(path=str(root))

    node_rows = list(adapter._read_nodes())
    edge_rows = list(adapter._read_edges())

    assert len(node_rows) == n_rows
    assert len(edge_rows) == n_rows

    # open_csv() was used (not read_csv, which _boom would have caught) --
    # once for nodes.tab, once for edges.csv.
    assert len(calls) == 2
    node_batch_counts, edge_batch_counts = calls

    # The real proof: rows arrived across multiple batches, and no single
    # batch held the whole file -- i.e. genuinely incremental, not a single
    # bulk read dressed up as "streaming".
    for batch_counts in (node_batch_counts, edge_batch_counts):
        assert len(batch_counts) > 1, "expected multiple batches, not one bulk read"
        assert sum(batch_counts) == n_rows
        assert max(batch_counts) < n_rows


# ---------------------------------------------------------------------------
# Real interop with the runner (AdapterProtocol compliance)
# ---------------------------------------------------------------------------

def test_runs_through_run_adapter_and_build_contribution_bundle(datasets_dir):
    adapter = PrimeKGAdapter(path=str(datasets_dir))
    offline = run_adapter(adapter)

    assert offline.name == "primekg"
    assert len(offline.nodes) == 4
    assert len(offline.edges) == 2

    from micromap_mapforge.integration.biocypher.ir import SourceRef

    bundle = build_contribution_bundle(
        offline,
        organization_id="test-org",
        source=SourceRef(kind="dir", path=str(datasets_dir), sha256="0" * 64),
    )
    assert bundle.organization_id == "test-org"
    assert len(bundle.nodes) == 4
    assert len(bundle.edges) == 2
