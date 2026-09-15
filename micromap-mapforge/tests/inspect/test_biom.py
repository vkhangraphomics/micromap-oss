"""#286 G2: BIOM feature-table inspector.

BIOM is the standard microbiome exchange format. The 1.0 encoding is documented
JSON — parsed natively here (no dependency), fully profiled. The 2.x encoding is
HDF5; until it's wired behind the [omics] extra, it's recognized and rejected
with an actionable convert-to-JSON step (not a bare "unsupported extension").

Observation metadata carries the taxonomic lineage; joined into the `;`-form that
inspect/structured.py already decomposes (d__/p__/.../s__), so `taxonomy` maps
straight onto the microbiome template's Taxon.
"""
import json
from pathlib import Path

import pytest

from micromap_mapforge.inspect.biom import inspect_biom
from micromap_mapforge.inspect.dispatch import inspect

_BIOM_1_0 = {
    "id": "test-table",
    "format": "Biological Observation Matrix 1.0.0",
    "format_url": "http://biom-format.org",
    "type": "OTU table",
    "generated_by": "test",
    "date": "2026-01-01T00:00:00",
    "matrix_type": "sparse",
    "matrix_element_type": "int",
    "shape": [3, 2],
    "rows": [
        {"id": "OTU1", "metadata": {"taxonomy": [
            "d__Bacteria", "p__Bacteroidota", "g__Bacteroides", "s__Bacteroides fragilis"]}},
        {"id": "OTU2", "metadata": {"taxonomy": [
            "d__Bacteria", "p__Bacillota", "g__Prevotella", "s__Prevotella copri"]}},
        {"id": "OTU3", "metadata": {"taxonomy": [
            "d__Archaea", "p__Euryarchaeota", "g__Methanobrevibacter",
            "s__Methanobrevibacter smithii"]}},
    ],
    "columns": [
        {"id": "SampleA", "metadata": None},
        {"id": "SampleB", "metadata": None},
    ],
    # sparse: [row_idx, col_idx, value]
    "data": [[0, 0, 5], [0, 1, 3], [1, 0, 2], [2, 1, 7]],
}


def _write(tmp_path: Path, doc: dict, name: str = "table.biom") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def test_biom_json_profiles_features_taxonomy_and_samples(tmp_path: Path):
    prof = inspect_biom(_write(tmp_path, _BIOM_1_0))
    assert prof.format == "biom"
    assert prof.row_count_estimate == 3                      # 3 features
    names = [c.name for c in prof.columns]
    assert "observation_id" in names and "taxonomy" in names
    assert "SampleA" in names and "SampleB" in names         # sample abundance columns
    assert "3 features" in prof.note and "2 samples" in prof.note


def test_biom_taxonomy_is_decomposed_as_rank_lineage(tmp_path: Path):
    prof = inspect_biom(_write(tmp_path, _BIOM_1_0))
    tax = next(c for c in prof.columns if c.name == "taxonomy")
    assert tax.structured is not None
    assert tax.structured["kind"] == "rank_lineage"
    assert tax.structured["components"] == ["domain", "phylum", "genus", "species"]


def test_biom_sample_column_carries_counts(tmp_path: Path):
    prof = inspect_biom(_write(tmp_path, _BIOM_1_0))
    a = next(c for c in prof.columns if c.name == "SampleA")
    assert a.inferred_type == "integer"
    assert set(a.samples) == {5, 2}          # SampleA: OTU1=5, OTU2=2 (OTU3 absent)
    assert a.null_rate == pytest.approx(1 / 3, abs=0.01)  # 1 of 3 features absent


def test_biom_dense_matrix_is_supported(tmp_path: Path):
    dense = dict(_BIOM_1_0, matrix_type="dense",
                 data=[[5, 3], [2, 0], [0, 7]])
    prof = inspect_biom(_write(tmp_path, dense))
    a = next(c for c in prof.columns if c.name == "SampleA")
    assert set(a.samples) == {5, 2}


def test_dispatch_routes_dot_biom(tmp_path: Path):
    prof = inspect(_write(tmp_path, _BIOM_1_0))
    assert prof.format == "biom"


def test_hdf5_biom_gets_actionable_convert_error(tmp_path: Path):
    p = tmp_path / "modern.biom"
    p.write_bytes(b"\x89HDF\r\n\x1a\n" + b"\x00" * 32)   # HDF5 magic
    with pytest.raises(ValueError, match="(?i)json|convert") as ei:
        inspect_biom(p)
    assert "unsupported file extension" not in str(ei.value)


def test_non_biom_json_is_rejected_clearly(tmp_path: Path):
    p = tmp_path / "notbiom.biom"
    p.write_text('{"hello": "world"}', encoding="utf-8")
    with pytest.raises(ValueError, match="(?i)biom"):
        inspect_biom(p)


def test_biom_profile_is_consumable_by_the_mapper(tmp_path: Path):
    # #286 acceptance: inspect -> map. The BIOM profile is a valid mapper input
    # (draft_heuristic_mapping validates against mapping.schema.json before
    # returning), and its taxonomy is structured so the LLM/heuristic mapper can
    # decompose it onto the microbiome template's Taxon.
    from micromap_mapforge.mapping.mapper import draft_heuristic_mapping

    prof = inspect_biom(_write(tmp_path, _BIOM_1_0))
    mapping = draft_heuristic_mapping(prof)  # defaults to the microbiome schema
    assert isinstance(mapping.get("entities"), list)
    tax = next(c for c in prof.columns if c.name == "taxonomy")
    assert tax.structured and tax.structured["kind"] == "rank_lineage"
