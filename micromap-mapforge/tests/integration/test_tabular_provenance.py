"""3b-2 (issue #126): structured provenance + confidence through the tabular path.

Pins the contract that tabular_to_ir populates IR nodes/edges with the same
structured provenance/confidence the BioCypher path already carries:

  - IRNode/IREdge.confidence ← the mapping's entity/relationship confidence
    (EXTRACTED/INFERRED/AMBIGUOUS).
  - IRNode/IREdge.provenance.source ← mapping.source.name (already in 3b-1).
  - IRNode/IREdge.provenance.method ← derived from confidence:
      EXTRACTED → 'curated' (the source explicitly states this fact)
      INFERRED  → 'mined'   (the source was processed to infer this)
      AMBIGUOUS → 'mined'   (still automated; multiple candidates rejected)
  - IRNode/IREdge.provenance.ref ← row-level reference if mapping.source
    declares a `ref_column` and that column is non-null on the row, else
    omitted.

Plus the provenance opt-out (#63 / PR #113):
  - serialize_bundle(include_provenance=False) skips emitting provenance_*
    keys on the Neo4j-property payload, even when IR carries them.
  - confidence and tier are NOT provenance and remain on the payload.

And one intentional divergence from emit/cypher.py:
  - The fall-through `source_origin: 'contributor'` row prop is dropped in
    the IR path. (existing=False) + (provenance.source=<mapping source>)
    is the complete signal; source_origin was redundant.
"""

from __future__ import annotations

import json
from pathlib import Path


from micromap_mapforge.confidence import Confidence
from micromap_mapforge.integration.biocypher.ir import (
    ContributionBundle,
    IRNode,
    SourceRef,
)
from micromap_mapforge.integration.biocypher.serialize import serialize_bundle
from micromap_mapforge.integration.tabular import tabular_to_ir
from micromap_mapforge.resolve.base import Candidate, ResolutionRow
from micromap_mapforge.resolve.pipeline import ResolutionReport


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mapping(*, taxon_conf: str | None = "EXTRACTED",
             disease_conf: str | None = "EXTRACTED",
             rel_conf: str | None = "INFERRED",
             ref_column: str | None = None) -> dict:
    source: dict = {"name": "disbiome", "format": "csv", "path": "/tmp/x.csv"}
    if ref_column is not None:
        source["ref_column"] = ref_column
    entities = [
        {"label": "Taxon", "match_on": "ncbi_taxid",
         "columns": {"ncbi_taxid": "tax_id", "name": "tax_name"}},
        {"label": "Disease", "match_on": "mondo_id",
         "columns": {"mondo_id": "disease_id", "name": "disease_name"}},
    ]
    if taxon_conf is not None:
        entities[0]["confidence"] = taxon_conf
    if disease_conf is not None:
        entities[1]["confidence"] = disease_conf
    rel = {"type": "ASSOCIATED_WITH",
           "from": "Taxon(ncbi_taxid=row.tax_id)",
           "to":   "Disease(mondo_id=row.disease_id)",
           "properties": {"evidence": "evidence_strength"}}
    if rel_conf is not None:
        rel["confidence"] = rel_conf
    return {"source": source, "entities": entities, "relationships": [rel]}


def _src() -> SourceRef:
    return SourceRef(kind="file", path="/tmp/x.csv", sha256="0" * 64)


def _resolved(label: str, term: str, mf: str, mv: str) -> ResolutionRow:
    return ResolutionRow(
        entity_label=label, source_term=term,
        candidates=[Candidate(
            node_id=f"{label.lower()}:{mv}", match_type=Confidence.EXTRACTED,
            score=1.0, reason="", properties={}, merge_field=mf, merge_value=mv,
        )],
    )


# ---------------------------------------------------------------------------
# IR-level: confidence + provenance.method
# ---------------------------------------------------------------------------


def test_irnode_confidence_set_from_entity_mapping():
    mapping = _mapping(taxon_conf="EXTRACTED", disease_conf="INFERRED")
    rows = [{"tax_id": "9606", "tax_name": "Homo sapiens",
             "disease_id": "MONDO:1", "disease_name": "Diabetes",
             "evidence_strength": "strong"}]
    report = ResolutionReport()
    bundle = tabular_to_ir(mapping=mapping, rows=rows, report=report,
                           organization_id="org-test", source_ref=_src())
    taxon = next(n for n in bundle.nodes if n.label == "Taxon")
    disease = next(n for n in bundle.nodes if n.label == "Disease")
    assert taxon.confidence == Confidence.EXTRACTED
    assert disease.confidence == Confidence.INFERRED


def test_irnode_provenance_method_extracted_maps_to_curated():
    mapping = _mapping(taxon_conf="EXTRACTED", disease_conf="EXTRACTED")
    rows = [{"tax_id": "9606", "tax_name": "H. sapiens",
             "disease_id": "MONDO:1", "disease_name": "D",
             "evidence_strength": "x"}]
    bundle = tabular_to_ir(mapping=mapping, rows=rows, report=ResolutionReport(),
                           organization_id="org", source_ref=_src())
    for n in bundle.nodes:
        assert n.provenance["method"] == "curated", n.label


def test_irnode_provenance_method_inferred_maps_to_mined():
    mapping = _mapping(taxon_conf="INFERRED", disease_conf="INFERRED")
    rows = [{"tax_id": "9606", "tax_name": "H. sapiens",
             "disease_id": "MONDO:1", "disease_name": "D",
             "evidence_strength": "x"}]
    bundle = tabular_to_ir(mapping=mapping, rows=rows, report=ResolutionReport(),
                           organization_id="org", source_ref=_src())
    for n in bundle.nodes:
        assert n.provenance["method"] == "mined", n.label


def test_irnode_provenance_method_ambiguous_maps_to_mined():
    mapping = _mapping(taxon_conf="AMBIGUOUS", disease_conf="AMBIGUOUS")
    rows = [{"tax_id": "9606", "tax_name": "H. sapiens",
             "disease_id": "MONDO:1", "disease_name": "D",
             "evidence_strength": "x"}]
    bundle = tabular_to_ir(mapping=mapping, rows=rows, report=ResolutionReport(),
                           organization_id="org", source_ref=_src())
    for n in bundle.nodes:
        assert n.provenance["method"] == "mined", n.label


def test_irnode_omits_method_when_entity_has_no_confidence():
    """If the mapping doesn't classify the entity, provenance.method is omitted —
    we don't fabricate a value."""
    mapping = _mapping(taxon_conf=None, disease_conf="EXTRACTED")
    rows = [{"tax_id": "9606", "tax_name": "H. sapiens",
             "disease_id": "MONDO:1", "disease_name": "D",
             "evidence_strength": "x"}]
    bundle = tabular_to_ir(mapping=mapping, rows=rows, report=ResolutionReport(),
                           organization_id="org", source_ref=_src())
    taxon = next(n for n in bundle.nodes if n.label == "Taxon")
    assert "method" not in taxon.provenance


def test_iredge_inherits_confidence_and_method_from_relationship_mapping():
    mapping = _mapping(rel_conf="INFERRED")
    rows = [{"tax_id": "9606", "tax_name": "H. sapiens",
             "disease_id": "MONDO:1", "disease_name": "D",
             "evidence_strength": "x"}]
    bundle = tabular_to_ir(mapping=mapping, rows=rows, report=ResolutionReport(),
                           organization_id="org", source_ref=_src())
    edge = next(e for e in bundle.edges if e.type == "ASSOCIATED_WITH")
    assert edge.confidence == Confidence.INFERRED
    assert edge.provenance["method"] == "mined"


# ---------------------------------------------------------------------------
# Row-level ref column
# ---------------------------------------------------------------------------


def test_ref_column_in_mapping_populates_provenance_ref():
    mapping = _mapping(ref_column="pmid")
    rows = [{"tax_id": "9606", "tax_name": "H. sapiens",
             "disease_id": "MONDO:1", "disease_name": "D",
             "evidence_strength": "strong", "pmid": "PMID:12345"}]
    bundle = tabular_to_ir(mapping=mapping, rows=rows, report=ResolutionReport(),
                           organization_id="org", source_ref=_src())
    taxon = next(n for n in bundle.nodes if n.label == "Taxon")
    assert taxon.provenance.get("ref") == "PMID:12345"


def test_ref_omitted_when_row_lacks_ref_value():
    mapping = _mapping(ref_column="pmid")
    rows = [{"tax_id": "9606", "tax_name": "H. sapiens",
             "disease_id": "MONDO:1", "disease_name": "D",
             "evidence_strength": "strong", "pmid": None}]
    bundle = tabular_to_ir(mapping=mapping, rows=rows, report=ResolutionReport(),
                           organization_id="org", source_ref=_src())
    taxon = next(n for n in bundle.nodes if n.label == "Taxon")
    assert "ref" not in taxon.provenance


def test_no_ref_column_in_mapping_means_no_ref_anywhere():
    mapping = _mapping(ref_column=None)
    rows = [{"tax_id": "9606", "tax_name": "H. sapiens",
             "disease_id": "MONDO:1", "disease_name": "D",
             "evidence_strength": "strong", "pmid": "PMID:99"}]
    bundle = tabular_to_ir(mapping=mapping, rows=rows, report=ResolutionReport(),
                           organization_id="org", source_ref=_src())
    for n in bundle.nodes:
        assert "ref" not in n.provenance


# ---------------------------------------------------------------------------
# Serializer opt-out: include_provenance flag
# ---------------------------------------------------------------------------


def _one_node_bundle_with_provenance(tmp_path: Path) -> ContributionBundle:
    return ContributionBundle(
        schema_version="1.0",
        schema_config={"name": "x"},
        organization_id="org-test",
        nodes=[
            IRNode(
                label="Taxon", id="9606",
                properties={"name": "Homo sapiens"},
                provenance={"source": "disbiome", "method": "curated", "ref": "PMID:1"},
                confidence=Confidence.EXTRACTED,
                merge_field="ncbi_taxid", existing=True,
            ),
        ],
        edges=[],
        source=SourceRef(kind="file", path=str(tmp_path / "x.csv"), sha256="0" * 64),
    )


def test_serialize_with_include_provenance_false_skips_provenance_props(tmp_path: Path):
    bundle = _one_node_bundle_with_provenance(tmp_path)
    out = tmp_path / "bundle"
    serialize_bundle(bundle, out_dir=out, include_provenance=False)
    params = json.loads((out / "cypher" / "nodes_Taxon.params.json").read_text(encoding="utf-8"))
    props = params["batch_ncbi_taxid"][0]["props"]
    assert "provenance_source" not in props
    assert "provenance_method" not in props
    assert "provenance_ref" not in props


def test_serialize_with_include_provenance_false_keeps_confidence(tmp_path: Path):
    """Confidence is not provenance — opt-out preserves it."""
    bundle = _one_node_bundle_with_provenance(tmp_path)
    out = tmp_path / "bundle"
    serialize_bundle(bundle, out_dir=out, include_provenance=False)
    params = json.loads((out / "cypher" / "nodes_Taxon.params.json").read_text(encoding="utf-8"))
    props = params["batch_ncbi_taxid"][0]["props"]
    assert props["confidence"] == "EXTRACTED"


def test_serialize_default_include_provenance_true_keeps_provenance(tmp_path: Path):
    """Regression guard: not passing include_provenance must not change behavior."""
    bundle = _one_node_bundle_with_provenance(tmp_path)
    out = tmp_path / "bundle"
    serialize_bundle(bundle, out_dir=out)  # default
    params = json.loads((out / "cypher" / "nodes_Taxon.params.json").read_text(encoding="utf-8"))
    props = params["batch_ncbi_taxid"][0]["props"]
    assert props["provenance_source"] == "disbiome"
    assert props["provenance_method"] == "curated"
    assert props["provenance_ref"] == "PMID:1"


# ---------------------------------------------------------------------------
# Intentional divergence: source_origin dropped
# ---------------------------------------------------------------------------


def test_tabular_ir_does_not_emit_source_origin_for_fall_through_nodes():
    """The legacy emit/cypher.py:100 sets `source_origin: 'contributor'` on
    fall-through props. The IR path drops it — (existing=False) and
    (provenance.source) carry the same signal without a redundant property.

    This is the second documented intentional divergence (alongside row
    dedup). 3b-4 (#128) will update goldens to match.
    """
    mapping = _mapping()
    rows = [{"tax_id": "777777", "tax_name": "Contributor-only",
             "disease_id": "MONDO:1", "disease_name": "D",
             "evidence_strength": "weak"}]
    bundle = tabular_to_ir(mapping=mapping, rows=rows, report=ResolutionReport(),
                           organization_id="org", source_ref=_src())
    taxon = next(n for n in bundle.nodes if n.label == "Taxon")
    assert taxon.existing is False
    assert "source_origin" not in taxon.properties
    # The signal that legacy carried via source_origin is now in provenance.source.
    assert taxon.provenance["source"] == "disbiome"
