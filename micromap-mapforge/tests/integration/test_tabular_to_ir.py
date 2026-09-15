"""tabular_to_ir() — tabular path's mapping+rows+report → ContributionBundle (3b-1, #125).

Pins the row-level decisions ``tabular_to_ir`` must reproduce from the legacy
``emit/cypher.py`` so the IR-routed bundle, after 3a serialization, is
behavior-equivalent (and ideally byte-equivalent) to today's ``mapforge emit``.

The four required cases come from #125's acceptance criteria:

  (a) all-resolved: every source row resolved to an existing graph node →
      every IRNode has ``existing=True`` and ``merge_field`` from the resolver.
  (b) all-fall-through: empty/missing resolution → every IRNode has
      ``existing=False`` and ``merge_field`` from the mapping's ``match_on``.
  (c) mixed: a single label has both resolved and fall-through rows.
  (d) cross-batch endpoint: an IREdge whose endpoint isn't represented as an
      IRNode in the bundle — the bundle still validates and the serializer's
      CROSS_BATCH path kicks in at apply time.
"""

from __future__ import annotations


from micromap_mapforge.confidence import Confidence
from micromap_mapforge.integration.biocypher.ir import (
    SourceRef,
    validate_bundle_dict,
    bundle_to_schema_dict,
)
from micromap_mapforge.integration.tabular import tabular_to_ir
from micromap_mapforge.resolve.base import Candidate, ResolutionRow
from micromap_mapforge.resolve.pipeline import ResolutionReport


# ---------------------------------------------------------------------------
# Fixtures — a minimal Taxon/Disease mapping shaped like the Disbiome example.
# ---------------------------------------------------------------------------


def _mapping() -> dict:
    return {
        "source": {
            "name": "test-tabular",
            "format": "csv",
            "path": "/tmp/test.csv",
        },
        "entities": [
            {
                "label": "Taxon",
                "match_on": "ncbi_taxid",
                "columns": {"ncbi_taxid": "tax_id", "name": "tax_name"},
            },
            {
                "label": "Disease",
                "match_on": "mondo_id",
                "columns": {"mondo_id": "disease_id", "name": "disease_name"},
            },
        ],
        "relationships": [
            {
                "type": "ASSOCIATED_WITH",
                "from": "Taxon(ncbi_taxid=row.tax_id)",
                "to":   "Disease(mondo_id=row.disease_id)",
                "properties": {"evidence": "evidence_strength"},
            }
        ],
    }


def _source_ref() -> SourceRef:
    return SourceRef(kind="file", path="/tmp/test.csv", sha256="0" * 64)


def _resolved(label: str, term: str, merge_field: str, merge_value: str) -> ResolutionRow:
    return ResolutionRow(
        entity_label=label,
        source_term=term,
        candidates=[
            Candidate(
                node_id=f"{label.lower()}:{merge_value}",
                match_type=Confidence.EXTRACTED,
                score=1.0,
                reason="curated",
                properties={},
                merge_field=merge_field,
                merge_value=merge_value,
            )
        ],
    )


# ---------------------------------------------------------------------------
# (a) all-resolved
# ---------------------------------------------------------------------------


def test_all_resolved_produces_existing_irnodes_with_resolver_merge_field():
    mapping = _mapping()
    rows = [
        {"tax_id": "9606", "tax_name": "Homo sapiens",
         "disease_id": "MONDO:0005148", "disease_name": "Type 2 diabetes",
         "evidence_strength": "strong"},
    ]
    report = ResolutionReport(
        resolved=[
            _resolved("Taxon", "9606", "ncbi_taxid", "9606"),
            _resolved("Disease", "MONDO:0005148", "mondo_id", "MONDO:0005148"),
        ],
    )
    bundle = tabular_to_ir(
        mapping=mapping, rows=rows, report=report,
        organization_id="org-test", source_ref=_source_ref(),
    )
    taxons = [n for n in bundle.nodes if n.label == "Taxon"]
    diseases = [n for n in bundle.nodes if n.label == "Disease"]
    assert len(taxons) == 1 and len(diseases) == 1
    assert taxons[0].existing is True
    assert taxons[0].merge_field == "ncbi_taxid"
    assert taxons[0].id == "9606"
    assert diseases[0].existing is True
    assert diseases[0].merge_field == "mondo_id"


# ---------------------------------------------------------------------------
# (b) all-fall-through
# ---------------------------------------------------------------------------


def test_all_fall_through_produces_contributor_irnodes_with_match_on_merge_field():
    mapping = _mapping()
    rows = [
        {"tax_id": "999999", "tax_name": "Contributor-only taxon",
         "disease_id": "CONT:0001", "disease_name": "Contributor-only disease",
         "evidence_strength": "weak"},
    ]
    report = ResolutionReport()  # no candidates anywhere
    bundle = tabular_to_ir(
        mapping=mapping, rows=rows, report=report,
        organization_id="org-test", source_ref=_source_ref(),
    )
    taxons = [n for n in bundle.nodes if n.label == "Taxon"]
    diseases = [n for n in bundle.nodes if n.label == "Disease"]
    assert len(taxons) == 1 and len(diseases) == 1
    assert taxons[0].existing is False
    assert taxons[0].merge_field == "ncbi_taxid"  # mapping's match_on
    assert taxons[0].id == "999999"
    assert diseases[0].existing is False
    assert diseases[0].merge_field == "mondo_id"


# ---------------------------------------------------------------------------
# (c) mixed
# ---------------------------------------------------------------------------


def test_mixed_resolved_and_fall_through_same_label_produces_both_kinds():
    mapping = _mapping()
    rows = [
        {"tax_id": "9606", "tax_name": "Homo sapiens",
         "disease_id": "MONDO:0005148", "disease_name": "T2D",
         "evidence_strength": "strong"},
        {"tax_id": "777777", "tax_name": "Contributor-only taxon",
         "disease_id": "MONDO:0005148", "disease_name": "T2D",
         "evidence_strength": "weak"},
    ]
    report = ResolutionReport(
        resolved=[
            _resolved("Taxon", "9606", "ncbi_taxid", "9606"),
            _resolved("Disease", "MONDO:0005148", "mondo_id", "MONDO:0005148"),
        ],
    )
    bundle = tabular_to_ir(
        mapping=mapping, rows=rows, report=report,
        organization_id="org-test", source_ref=_source_ref(),
    )
    taxons = {n.id: n for n in bundle.nodes if n.label == "Taxon"}
    assert set(taxons.keys()) == {"9606", "777777"}
    assert taxons["9606"].existing is True
    assert taxons["9606"].merge_field == "ncbi_taxid"
    assert taxons["777777"].existing is False
    assert taxons["777777"].merge_field == "ncbi_taxid"  # falls back to match_on


# ---------------------------------------------------------------------------
# (d) cross-batch endpoint — edge endpoint not represented in nodes
# ---------------------------------------------------------------------------


def test_edge_with_endpoint_missing_from_nodes_still_emits_edge():
    """The legacy emitter still emits Cypher even when an edge endpoint isn't
    materialized as a node (the source row has the disease_id but no Disease
    entity row exists separately). tabular_to_ir must mirror: emit the edge,
    let the serializer's CROSS_BATCH path handle the apply-time MATCH."""
    mapping = _mapping()
    # Drop the Disease entity definition so no Disease IRNodes are produced.
    mapping["entities"] = [mapping["entities"][0]]  # Taxon only
    rows = [
        {"tax_id": "9606", "tax_name": "Homo sapiens",
         "disease_id": "MONDO:0005148", "disease_name": "T2D",
         "evidence_strength": "strong"},
    ]
    report = ResolutionReport()
    bundle = tabular_to_ir(
        mapping=mapping, rows=rows, report=report,
        organization_id="org-test", source_ref=_source_ref(),
    )
    edges = [e for e in bundle.edges if e.type == "ASSOCIATED_WITH"]
    assert len(edges) == 1
    assert edges[0].from_id == "9606"
    assert edges[0].to_id == "MONDO:0005148"
    # Bundle still validates against the IR schema.
    validate_bundle_dict(bundle_to_schema_dict(bundle))


# ---------------------------------------------------------------------------
# Behavior the tabular path must preserve from emit/cypher.py
# ---------------------------------------------------------------------------


def test_rows_with_null_match_term_are_skipped():
    """Mirrors emit/cypher.py: a row whose match_on source column is None or ''
    contributes no IRNode for that label."""
    mapping = _mapping()
    rows = [
        {"tax_id": "", "tax_name": "Missing taxid",
         "disease_id": "MONDO:0005148", "disease_name": "T2D",
         "evidence_strength": "strong"},
        {"tax_id": None, "tax_name": "Also missing",
         "disease_id": "MONDO:0005148", "disease_name": "T2D",
         "evidence_strength": "strong"},
    ]
    report = ResolutionReport()
    bundle = tabular_to_ir(
        mapping=mapping, rows=rows, report=report,
        organization_id="org-test", source_ref=_source_ref(),
    )
    taxons = [n for n in bundle.nodes if n.label == "Taxon"]
    assert taxons == []


def test_provenance_source_comes_from_mapping_source_name():
    """3b-1 sets provenance.source from mapping.source.name. (3b-2 will add
    method/ref derivation; that's deliberately out of scope here.)"""
    mapping = _mapping()
    rows = [{"tax_id": "9606", "tax_name": "Homo sapiens",
             "disease_id": "MONDO:0005148", "disease_name": "T2D",
             "evidence_strength": "strong"}]
    report = ResolutionReport(
        resolved=[_resolved("Taxon", "9606", "ncbi_taxid", "9606")],
    )
    bundle = tabular_to_ir(
        mapping=mapping, rows=rows, report=report,
        organization_id="org-test", source_ref=_source_ref(),
    )
    taxon = next(n for n in bundle.nodes if n.label == "Taxon")
    assert taxon.provenance["source"] == "test-tabular"


def test_duplicate_source_term_produces_single_irnode():
    """Two rows referencing the same Taxon → one IRNode (resolver dedupes by
    (label, source_term); the IR producer mirrors that)."""
    mapping = _mapping()
    rows = [
        {"tax_id": "9606", "tax_name": "Homo sapiens",
         "disease_id": "MONDO:0005148", "disease_name": "T2D",
         "evidence_strength": "strong"},
        {"tax_id": "9606", "tax_name": "Homo sapiens",
         "disease_id": "MONDO:0004975", "disease_name": "Crohn's",
         "evidence_strength": "moderate"},
    ]
    report = ResolutionReport(
        resolved=[_resolved("Taxon", "9606", "ncbi_taxid", "9606")],
    )
    bundle = tabular_to_ir(
        mapping=mapping, rows=rows, report=report,
        organization_id="org-test", source_ref=_source_ref(),
    )
    taxons = [n for n in bundle.nodes if n.label == "Taxon"]
    assert len(taxons) == 1


def test_bundle_validates_against_ir_schema():
    """End-to-end: anything tabular_to_ir produces must pass the JSON schema."""
    mapping = _mapping()
    rows = [{"tax_id": "9606", "tax_name": "Homo sapiens",
             "disease_id": "MONDO:0005148", "disease_name": "T2D",
             "evidence_strength": "strong"}]
    report = ResolutionReport(
        resolved=[
            _resolved("Taxon", "9606", "ncbi_taxid", "9606"),
            _resolved("Disease", "MONDO:0005148", "mondo_id", "MONDO:0005148"),
        ],
    )
    bundle = tabular_to_ir(
        mapping=mapping, rows=rows, report=report,
        organization_id="org-test", source_ref=_source_ref(),
    )
    validate_bundle_dict(bundle_to_schema_dict(bundle))


# ---------------------------------------------------------------------------
# Regression: derived-key match_on must NOT drop the display name (fall-through).
# When match_on is a derived key not present in columns (e.g. Disease
# name_normalized), source_column falls back to the name column. The new node
# must still carry `name` — otherwise from-scratch graphs get nameless,
# unsearchable nodes. (Was a real bug; see closed PR #161.)
# ---------------------------------------------------------------------------


def test_fall_through_derived_match_on_keeps_display_name():
    mapping = {
        "source": {"name": "t", "format": "csv", "path": "/tmp/t.csv"},
        "entities": [
            {
                "label": "Disease",
                "match_on": "name_normalized",       # derived key, NOT a column
                "columns": {"name": "disease_name"},  # source_column => disease_name
            },
        ],
        "relationships": [],
    }
    rows = [{"disease_name": "Crohn disease"}]
    report = ResolutionReport(resolved=[])  # fall-through (nothing resolved)

    bundle = tabular_to_ir(
        mapping=mapping, rows=rows, report=report,
        organization_id="org-test", source_ref=_source_ref(),
    )
    diseases = [n for n in bundle.nodes if n.label == "Disease"]
    assert len(diseases) == 1
    d = diseases[0]
    assert d.existing is False
    assert d.merge_field == "name_normalized"
    # The display name must be preserved on the new node.
    assert d.properties.get("name") == "Crohn disease"


def test_resolved_derived_match_on_does_not_clobber_source_column():
    """Conservative on resolve: when matching an existing canonical node, the
    source-term column is not re-written (avoids clobbering canonical values)."""
    mapping = {
        "source": {"name": "t", "format": "csv", "path": "/tmp/t.csv"},
        "entities": [
            {
                "label": "Disease",
                "match_on": "name_normalized",
                "columns": {"name": "disease_name"},
            },
        ],
        "relationships": [],
    }
    rows = [{"disease_name": "Crohn disease"}]
    report = ResolutionReport(
        resolved=[_resolved("Disease", "Crohn disease", "name_normalized", "crohn disease")],
    )
    bundle = tabular_to_ir(
        mapping=mapping, rows=rows, report=report,
        organization_id="org-test", source_ref=_source_ref(),
    )
    d = next(n for n in bundle.nodes if n.label == "Disease")
    assert d.existing is True
    assert "name" not in d.properties  # source column not re-written on resolve
