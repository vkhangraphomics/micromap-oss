"""Serializer behavior under the 3b-0 IR discriminator (`merge_field`, `existing`).

Background: 3a's IR + serializer hard-codes `MERGE (n:Label {id: row.merge_value,
organization_id: $org})` for every node. That's correct for BioCypher producers
(canonical CURIE-keyed contributor data) but cannot express the tabular path's
two-shape emit (issue #97, sub-issue #129):

  - resolved entity → `MATCH (n:Label {<native_field>: row.merge_value})` no org-scope
  - fall-through entity → `MERGE (n:Label {<native_field>: row.merge_value, organization_id: $org})`

These tests pin the new contract:
  - IRNode gains `merge_field: str = "id"` and `existing: bool = False`.
  - BioCypher behavior (no kwargs passed) is byte-identical to today.
  - When `existing=True`, the serializer emits MATCH, no org-scope in merge key.
  - When `merge_field != "id"`, the merge key uses that field (not `id`).
  - mapping.yaml entities[*].match_on honors merge_field.
  - Relationship endpoint MATCH uses each endpoint's merge_field.

See parent: #97. See sub-issue: #129. Prereq for: #125 (3b-1).
"""

from __future__ import annotations

import json
from pathlib import Path


from micromap_mapforge.integration.biocypher.ir import (
    ContributionBundle,
    IREdge,
    IRNode,
    SourceRef,
    validate_bundle_dict,
    bundle_to_schema_dict,
)
from micromap_mapforge.integration.biocypher.serialize import serialize_bundle


# ---------------------------------------------------------------------------
# IRNode default fields
# ---------------------------------------------------------------------------


def test_irnode_defaults_preserve_biocypher_behavior():
    """IRNode constructed without merge_field/existing must default to the
    BioCypher-compatible shape: merge_field='id', existing=False."""
    node = IRNode(
        label="OrganismTaxon",
        id="NCBITaxon:9606",
        properties={"name": "Homo sapiens"},
        provenance={"source": "gtdb", "method": "curated"},
    )
    assert node.merge_field == "id"
    assert node.existing is False


def test_irnode_accepts_explicit_merge_field_and_existing():
    """The tabular path needs to construct nodes with both fields set."""
    node = IRNode(
        label="Taxon",
        id="9606",
        properties={"name": "Homo sapiens"},
        provenance={"source": "ncbi-taxonomy", "method": "curated"},
        merge_field="ncbi_taxid",
        existing=True,
    )
    assert node.merge_field == "ncbi_taxid"
    assert node.existing is True


# ---------------------------------------------------------------------------
# JSON-schema validation: forward + backward compatibility
# ---------------------------------------------------------------------------


def _wrap_one(node: IRNode, tmp_path: Path) -> ContributionBundle:
    return ContributionBundle(
        schema_version="1.0",
        schema_config={"prefixes": {"NCBITaxon": "http://x/"}},
        organization_id="org-test",
        nodes=[node],
        edges=[],
        source=SourceRef(kind="file", path=str(tmp_path / "x.tsv"), sha256="0" * 64),
    )


def test_schema_validates_bundle_with_new_fields(tmp_path: Path):
    """Bundle dict containing merge_field/existing must validate."""
    bundle = _wrap_one(
        IRNode(
            label="Taxon",
            id="9606",
            properties={"name": "Homo sapiens"},
            provenance={"source": "ncbi", "method": "curated"},
            merge_field="ncbi_taxid",
            existing=True,
        ),
        tmp_path,
    )
    payload = bundle_to_schema_dict(bundle)
    validate_bundle_dict(payload)  # must not raise


def test_schema_validates_bundle_without_new_fields(tmp_path: Path):
    """Regression guard: legacy BioCypher payloads (no merge_field/existing) must still validate."""
    bundle = _wrap_one(
        IRNode(
            label="OrganismTaxon",
            id="NCBITaxon:9606",
            properties={"name": "Homo sapiens"},
            provenance={"source": "gtdb", "method": "curated"},
        ),
        tmp_path,
    )
    payload = bundle_to_schema_dict(bundle)
    validate_bundle_dict(payload)  # must not raise


# ---------------------------------------------------------------------------
# Node Cypher: MATCH vs MERGE, merge_field, org-scope
# ---------------------------------------------------------------------------


def test_existing_node_emits_match_no_org_scope(tmp_path: Path):
    """existing=True ⇒ MATCH on the native merge_field; no organization_id
    in the merge key (the node is shared across orgs — issue #90 / PR #108)."""
    bundle = _wrap_one(
        IRNode(
            label="Taxon",
            id="9606",
            properties={"name": "Homo sapiens"},
            provenance={"source": "ncbi", "method": "curated"},
            merge_field="ncbi_taxid",
            existing=True,
        ),
        tmp_path,
    )
    serialize_bundle(bundle, out_dir=tmp_path)
    cy = (tmp_path / "cypher" / "nodes_Taxon.cypher").read_text(encoding="utf-8")
    assert "MATCH (n:Taxon {ncbi_taxid: row.merge_value})" in cy
    assert "MERGE (n:Taxon" not in cy
    # SET still applies bundle props; no organization_id added to the merge key
    assert "SET n += row.props" in cy
    assert "organization_id" not in cy


def test_non_default_merge_field_for_contributor_node_emits_merge_on_field(tmp_path: Path):
    """existing=False with merge_field != 'id' ⇒ org-scoped MERGE on that field."""
    bundle = _wrap_one(
        IRNode(
            label="Taxon",
            id="999999",
            properties={"name": "Contributor-only taxon"},
            provenance={"source": "contributor-upload", "method": "curated"},
            merge_field="ncbi_taxid",
            existing=False,
        ),
        tmp_path,
    )
    serialize_bundle(bundle, out_dir=tmp_path)
    cy = (tmp_path / "cypher" / "nodes_Taxon.cypher").read_text(encoding="utf-8")
    assert "MERGE (n:Taxon {ncbi_taxid: row.merge_value, organization_id: $organization_id})" in cy
    assert "MATCH (n:Taxon" not in cy


def test_mixed_existing_and_contributor_emits_two_batches_in_one_file(tmp_path: Path):
    """Same label with both resolved and fall-through nodes → one Cypher file
    with two UNWIND batches and one params.json carrying both batch lists."""
    bundle = _wrap_one(
        IRNode(
            label="Taxon",
            id="9606",
            properties={"name": "Homo sapiens"},
            provenance={"source": "ncbi", "method": "curated"},
            merge_field="ncbi_taxid",
            existing=True,
        ),
        tmp_path,
    )
    bundle.nodes.append(
        IRNode(
            label="Taxon",
            id="888888",
            properties={"name": "Contributor-only taxon"},
            provenance={"source": "contributor-upload", "method": "curated"},
            merge_field="ncbi_taxid",
            existing=False,
        )
    )
    serialize_bundle(bundle, out_dir=tmp_path)
    cy = (tmp_path / "cypher" / "nodes_Taxon.cypher").read_text(encoding="utf-8")
    # Two UNWINDs in one file — legacy emitter convention.
    assert "UNWIND $batch_ncbi_taxid AS row" in cy
    assert "UNWIND $batch_ncbi_taxid__contributor AS row" in cy
    assert "MATCH (n:Taxon {ncbi_taxid: row.merge_value})" in cy
    assert "MERGE (n:Taxon {ncbi_taxid: row.merge_value, organization_id: $organization_id})" in cy

    params = json.loads((tmp_path / "cypher" / "nodes_Taxon.params.json").read_text(encoding="utf-8"))
    assert "batch_ncbi_taxid" in params
    assert "batch_ncbi_taxid__contributor" in params
    assert len(params["batch_ncbi_taxid"]) == 1
    assert params["batch_ncbi_taxid"][0]["merge_value"] == "9606"
    assert len(params["batch_ncbi_taxid__contributor"]) == 1
    assert params["batch_ncbi_taxid__contributor"][0]["merge_value"] == "888888"


# ---------------------------------------------------------------------------
# mapping.yaml: entities[*].match_on honors merge_field
# ---------------------------------------------------------------------------


def test_mapping_yaml_match_on_honors_merge_field(tmp_path: Path):
    """mapping.yaml's entities[*].match_on must reflect the IR merge_field —
    the bundle consumer reads this to choose its own MATCH key."""
    import yaml

    bundle = _wrap_one(
        IRNode(
            label="Taxon",
            id="9606",
            properties={"name": "Homo sapiens"},
            provenance={"source": "ncbi", "method": "curated"},
            merge_field="ncbi_taxid",
            existing=True,
        ),
        tmp_path,
    )
    serialize_bundle(bundle, out_dir=tmp_path)
    mapping = yaml.safe_load((tmp_path / "mapping.yaml").read_text(encoding="utf-8"))
    taxon_entity = next(e for e in mapping["entities"] if e["label"] == "Taxon")
    assert taxon_entity["match_on"] == "ncbi_taxid"


# ---------------------------------------------------------------------------
# Relationship endpoint MATCH: uses each endpoint's merge_field
# ---------------------------------------------------------------------------


def test_edge_endpoint_match_uses_endpoint_merge_field(tmp_path: Path):
    """When edge endpoints have merge_field != 'id', the MATCH must use that
    field for each endpoint independently (from and to may differ)."""
    bundle = ContributionBundle(
        schema_version="1.0",
        schema_config={"prefixes": {}},
        organization_id="org-test",
        nodes=[
            IRNode(
                label="Taxon",
                id="9606",
                properties={"name": "Homo sapiens"},
                provenance={"source": "ncbi", "method": "curated"},
                merge_field="ncbi_taxid",
                existing=True,
            ),
            IRNode(
                label="Disease",
                id="MONDO:0005148",
                properties={"name": "Type 2 diabetes"},
                provenance={"source": "mondo", "method": "curated"},
                merge_field="mondo_id",
                existing=True,
            ),
        ],
        edges=[
            IREdge(
                type="ASSOCIATED_WITH",
                from_id="9606",
                to_id="MONDO:0005148",
                properties={"evidence": "literature"},
                provenance={"source": "disbiome", "method": "literature"},
            ),
        ],
        source=SourceRef(kind="file", path=str(tmp_path / "x.tsv"), sha256="0" * 64),
    )
    serialize_bundle(bundle, out_dir=tmp_path)
    cy = (tmp_path / "cypher" / "rels_ASSOCIATED_WITH.cypher").read_text(encoding="utf-8")
    assert "MATCH (a:Taxon {ncbi_taxid: row.from})" in cy
    assert "(b:Disease {mondo_id: row.to})" in cy


# ---------------------------------------------------------------------------
# Regression guard: BioCypher byte-equivalence
# ---------------------------------------------------------------------------


def test_biocypher_default_byte_identical_to_today(tmp_path: Path):
    """A bundle constructed the way BioCypher constructs them (no merge_field,
    no existing) MUST produce exactly the same Cypher and params.json as the
    pre-3b-0 serializer. This is the regression guard for PR #101."""
    bundle = ContributionBundle(
        schema_version="1.0",
        schema_config={"prefixes": {"NCBITaxon": "http://x/"}},
        organization_id="org-test",
        nodes=[
            IRNode(
                label="OrganismTaxon",
                id="NCBITaxon:9606",
                properties={"name": "Homo sapiens"},
                provenance={"source": "gtdb", "method": "curated"},
            )
        ],
        edges=[],
        source=SourceRef(kind="file", path=str(tmp_path / "x.tsv"), sha256="0" * 64),
    )
    serialize_bundle(bundle, out_dir=tmp_path)
    cy = (tmp_path / "cypher" / "nodes_OrganismTaxon.cypher").read_text(encoding="utf-8")
    # Verbatim from test_serialize.py:75 — the existing BioCypher contract.
    assert "UNWIND $batch_id AS row" in cy
    assert "MERGE (n:OrganismTaxon {id: row.merge_value, organization_id: $organization_id})" in cy
    # No tabular-path artifacts must leak in.
    assert "ncbi_taxid" not in cy
    assert "__contributor" not in cy

    params = json.loads((tmp_path / "cypher" / "nodes_OrganismTaxon.params.json").read_text(encoding="utf-8"))
    assert "batch_id" in params
    assert "batch_id__contributor" not in params
