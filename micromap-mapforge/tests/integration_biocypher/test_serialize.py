"""Serializer: IR → existing bundle. See §15.2 / §15.3 for the design decisions."""

import json
from pathlib import Path

import pytest
import yaml

from micromap_mapforge.confidence import Confidence
from micromap_mapforge.integration.biocypher.ir import (
    ContributionBundle,
    IREdge,
    IRNode,
    SourceRef,
)
from micromap_mapforge.integration.biocypher.serialize import serialize_bundle


def _bundle_one_node() -> ContributionBundle:
    return ContributionBundle(
        schema_version="1.0",
        schema_config={"prefixes": {"NCBITaxon": "http://purl.obolibrary.org/obo/NCBITaxon_"}},
        organization_id="org-test",
        nodes=[
            IRNode(
                label="OrganismTaxon",
                id="NCBITaxon:9606",
                properties={"name": "Homo sapiens"},
                provenance={"source": "gtdb", "method": "curated"},
                confidence=Confidence.EXTRACTED,
            )
        ],
        edges=[],
        source=SourceRef(kind="file", path="/tmp/gtdb.tsv", sha256="0" * 64),
    )


def test_serialize_writes_mapping_yaml_with_schema_adapter_format(tmp_path: Path):
    serialize_bundle(_bundle_one_node(), out_dir=tmp_path)
    mapping = yaml.safe_load((tmp_path / "mapping.yaml").read_text(encoding="utf-8"))
    assert mapping["source"]["format"] == "schema_adapter"
    assert mapping["source"]["name"] == "schema-adapter-source"
    assert mapping["source"]["path"] == "/tmp/gtdb.tsv"


def test_serialize_mapping_yaml_has_one_entity_per_node_label(tmp_path: Path):
    bundle = _bundle_one_node()
    bundle.nodes.append(
        IRNode(
            label="Disease",
            id="MONDO:0005148",
            properties={"name": "Type 2 diabetes"},
            provenance={"source": "mondo", "method": "curated"},
        )
    )
    serialize_bundle(bundle, out_dir=tmp_path)
    mapping = yaml.safe_load((tmp_path / "mapping.yaml").read_text(encoding="utf-8"))
    labels = sorted(e["label"] for e in mapping["entities"])
    assert labels == ["Disease", "OrganismTaxon"]
    # match_on is always 'id' for adapter-sourced bundles (IR carries CURIEs).
    assert all(e["match_on"] == "id" for e in mapping["entities"])


def test_serialize_emits_node_cypher_file_per_label(tmp_path: Path):
    serialize_bundle(_bundle_one_node(), out_dir=tmp_path)
    cypher_dir = tmp_path / "cypher"
    assert (cypher_dir / "nodes_OrganismTaxon.cypher").exists()
    assert (cypher_dir / "nodes_OrganismTaxon.params.json").exists()


def test_serialize_node_cypher_uses_unwind_with_organization_id(tmp_path: Path):
    serialize_bundle(_bundle_one_node(), out_dir=tmp_path)
    cy = (tmp_path / "cypher" / "nodes_OrganismTaxon.cypher").read_text(encoding="utf-8")
    assert "UNWIND $batch_id AS row" in cy
    assert "MERGE (n:OrganismTaxon {id: row.merge_value, organization_id: $organization_id})" in cy


def test_serialize_node_params_carry_provenance_and_confidence_as_properties(tmp_path: Path):
    serialize_bundle(_bundle_one_node(), out_dir=tmp_path)
    params = json.loads((tmp_path / "cypher" / "nodes_OrganismTaxon.params.json").read_text(encoding="utf-8"))
    items = params["batch_id"]
    assert len(items) == 1
    props = items[0]["props"]
    assert props["provenance_source"] == "gtdb"
    assert props["provenance_method"] == "curated"
    assert props["confidence"] == "EXTRACTED"


def test_serialize_relationships_emit_labeled_match_endpoints(tmp_path: Path):
    bundle = _bundle_one_node()
    # Add the endpoint node + an edge.
    bundle.nodes.append(IRNode(
        label="OrganismTaxon",
        id="NCBITaxon:9605",
        properties={"name": "Homo"},
        provenance={"source": "gtdb", "method": "curated"},
    ))
    bundle.edges.append(IREdge(
        type="MEMBER_OF",
        from_id="NCBITaxon:9606",
        to_id="NCBITaxon:9605",
        properties={"evidence": "ncbi"},
        provenance={"source": "gtdb", "method": "curated"},
        confidence=Confidence.EXTRACTED,
    ))
    serialize_bundle(bundle, out_dir=tmp_path)
    cy = (tmp_path / "cypher" / "rels_MEMBER_OF.cypher").read_text(encoding="utf-8")
    # §15.2: labeled endpoints from IR node table.
    assert "MATCH (a:OrganismTaxon {id: row.from})" in cy
    assert "(b:OrganismTaxon {id: row.to})" in cy
    assert "MERGE (a)-[r:MEMBER_OF {organization_id: $organization_id}]->(b)" in cy

    params = json.loads((tmp_path / "cypher" / "rels_MEMBER_OF.params.json").read_text(encoding="utf-8"))
    items = params["batch_id__id"]
    assert items[0]["from"] == "NCBITaxon:9606"
    assert items[0]["to"] == "NCBITaxon:9605"
    assert items[0]["props"]["evidence"] == "ncbi"
    assert items[0]["props"]["provenance_source"] == "gtdb"
    assert items[0]["props"]["confidence"] == "EXTRACTED"


def test_serialize_relationship_type_with_special_characters_is_backtick_escaped(tmp_path: Path):
    """#374: PrimeKG's real raw `relation` value for one edge type is literally
    "off-label use" (space + hyphen) — naive f-string interpolation into Cypher
    produces a syntax error (`r:OFF-LABEL` parses as subtraction). Neo4j's
    standard fix is backtick-quoting the identifier."""
    bundle = _bundle_one_node()
    bundle.nodes.append(IRNode(
        label="OrganismTaxon",
        id="NCBITaxon:9605",
        properties={"name": "Homo"},
        provenance={"source": "gtdb", "method": "curated"},
    ))
    bundle.edges.append(IREdge(
        type="OFF-LABEL USE",
        from_id="NCBITaxon:9606",
        to_id="NCBITaxon:9605",
        properties={},
        provenance={"source": "primekg"},
        confidence=Confidence.EXTRACTED,
    ))
    serialize_bundle(bundle, out_dir=tmp_path)
    cy = (tmp_path / "cypher" / "rels_OFF-LABEL_USE.cypher").read_text(encoding="utf-8")
    assert "MERGE (a)-[r:`OFF-LABEL USE` {organization_id: $organization_id}]->(b)" in cy
    # A raw, unescaped interpolation would be invalid Cypher — must never appear.
    assert "[r:OFF-LABEL USE " not in cy


def test_serialize_node_label_with_special_characters_is_backtick_escaped(tmp_path: Path):
    bundle = ContributionBundle(
        schema_version="1.0",
        schema_config={"prefixes": {}},
        organization_id="org-test",
        nodes=[
            IRNode(
                label="Off-Label Use",
                id="X:1",
                properties={"name": "example"},
                provenance={"source": "primekg"},
            )
        ],
        edges=[],
        source=SourceRef(kind="file", path="/tmp/x.tsv", sha256="0" * 64),
    )
    serialize_bundle(bundle, out_dir=tmp_path)
    cy = (tmp_path / "cypher" / "nodes_Off-Label_Use.cypher").read_text(encoding="utf-8")
    assert "MERGE (n:`Off-Label Use` {id: row.merge_value, organization_id: $organization_id})" in cy


def test_serialize_clean_identifiers_are_not_backtick_escaped(tmp_path: Path):
    """Existing well-formed labels/types (the overwhelming common case) must stay
    byte-identical — no gratuitous backticks on identifiers that never needed them."""
    bundle = _bundle_one_node()
    bundle.nodes.append(IRNode(
        label="OrganismTaxon",
        id="NCBITaxon:9605",
        properties={"name": "Homo"},
        provenance={"source": "gtdb"},
    ))
    bundle.edges.append(IREdge(
        type="MEMBER_OF",
        from_id="NCBITaxon:9606",
        to_id="NCBITaxon:9605",
        properties={},
        provenance={"source": "gtdb"},
        confidence=Confidence.EXTRACTED,
    ))
    serialize_bundle(bundle, out_dir=tmp_path)
    node_cy = (tmp_path / "cypher" / "nodes_OrganismTaxon.cypher").read_text(encoding="utf-8")
    rel_cy = (tmp_path / "cypher" / "rels_MEMBER_OF.cypher").read_text(encoding="utf-8")
    assert "`" not in node_cy
    assert "`" not in rel_cy


def test_serialize_populates_mapping_relationships_block(tmp_path: Path):
    bundle = _bundle_one_node()
    bundle.nodes.append(IRNode(
        label="OrganismTaxon",
        id="NCBITaxon:9605",
        properties={"name": "Homo"},
        provenance={"source": "gtdb", "method": "curated"},
    ))
    bundle.edges.append(IREdge(
        type="MEMBER_OF",
        from_id="NCBITaxon:9606",
        to_id="NCBITaxon:9605",
        properties={"evidence": "ncbi"},
        provenance={"source": "gtdb", "method": "curated"},
    ))
    serialize_bundle(bundle, out_dir=tmp_path)
    mapping = yaml.safe_load((tmp_path / "mapping.yaml").read_text(encoding="utf-8"))
    # §15.3: populated, not [].
    assert len(mapping["relationships"]) == 1
    rel = mapping["relationships"][0]
    assert rel["type"] == "MEMBER_OF"
    assert rel["from"] == "OrganismTaxon(id=row.from)"
    assert rel["to"]   == "OrganismTaxon(id=row.to)"
    # Property-mapping block enumerates non-system props.
    assert "evidence" in rel["properties"]


def test_serialize_writes_routing_yaml_default_destination(tmp_path: Path):
    serialize_bundle(_bundle_one_node(), out_dir=tmp_path)
    routing = yaml.safe_load((tmp_path / "routing.yaml").read_text(encoding="utf-8"))
    assert routing["destination"] == "micromap-core"
    assert routing["organization_id"] == "org-test"


def test_serialize_writes_manifest_json_via_emit_bundle(tmp_path: Path):
    serialize_bundle(_bundle_one_node(), out_dir=tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["approved"] is False
    # write_manifest sha256s every file it finds; sanity-check at least one.
    assert any(name.startswith("cypher/nodes_") for name in manifest["files"])


def test_serialize_falls_back_to_unlabeled_match_when_endpoint_label_unknown(tmp_path: Path):
    bundle = _bundle_one_node()
    # to_id has no corresponding IRNode → label unresolvable.
    bundle.edges.append(IREdge(
        type="MENTIONED_IN",
        from_id="NCBITaxon:9606",
        to_id="PMID:12345",
        properties={"context": "abstract"},
        provenance={"source": "pubmed", "method": "mined"},
    ))
    serialize_bundle(bundle, out_dir=tmp_path)
    cy = (tmp_path / "cypher" / "rels_MENTIONED_IN.cypher").read_text(encoding="utf-8")
    # 'b' side falls back to label-less (§15.2 fallback clause).
    assert "(b {id: row.to})" in cy
    # The labeled 'a' side still resolves.
    assert "(a:OrganismTaxon {id: row.from})" in cy

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    # The cross-batch-warning is written into the bundle's WARNINGS.md file.
    assert "WARNINGS.md" in manifest["files"]
    warnings = (tmp_path / "WARNINGS.md").read_text(encoding="utf-8")
    assert "MENTIONED_IN" in warnings
    assert "to_id=PMID:12345" in warnings


def test_serialize_no_warnings_when_all_endpoints_resolve(tmp_path: Path):
    bundle = _bundle_one_node()
    bundle.nodes.append(IRNode(
        label="OrganismTaxon",
        id="NCBITaxon:9605",
        properties={"name": "Homo"},
        provenance={"source": "gtdb", "method": "curated"},
    ))
    bundle.edges.append(IREdge(
        type="MEMBER_OF",
        from_id="NCBITaxon:9606",
        to_id="NCBITaxon:9605",
        properties={},
        provenance={"source": "gtdb", "method": "curated"},
    ))
    serialize_bundle(bundle, out_dir=tmp_path)
    assert not (tmp_path / "WARNINGS.md").exists()


def test_serialize_bundle_raises_on_empty_nodes(tmp_path: Path):
    """Critical #4: 0-node bundles violate mapping.schema's entities.minItems=1."""
    bundle = ContributionBundle(
        schema_version="1.0",
        schema_config={"prefixes": {"NCBITaxon": "http://x/"}},
        organization_id="org-test",
        nodes=[],
        edges=[],
        source=SourceRef(kind="file", path=str(tmp_path / "x.tsv"), sha256="0" * 64),
    )
    out = tmp_path / "bundle"
    with pytest.raises(ValueError, match="at least one entity"):
        serialize_bundle(bundle, out)


def test_serialize_bundle_sanitizes_unsafe_label_in_filename(tmp_path: Path):
    """Critical #2: Biolink CURIE labels like 'biolink:OrganismTaxon' must not break filenames."""
    bundle = ContributionBundle(
        schema_version="1.0",
        schema_config={"prefixes": {}},
        organization_id="org-test",
        nodes=[
            IRNode(
                label="biolink:OrganismTaxon",
                id="NCBITaxon:9606",
                properties={"name": "Homo sapiens"},
                provenance={"source": "gtdb", "method": "curated"},
            ),
        ],
        edges=[],
        source=SourceRef(kind="file", path=str(tmp_path / "x.tsv"), sha256="0" * 64),
    )
    out = tmp_path / "bundle"
    serialize_bundle(bundle, out)
    # filename has the colon replaced; the label inside the Cypher body is preserved
    assert (out / "cypher" / "nodes_biolink_OrganismTaxon.cypher").exists()
    cypher_text = (out / "cypher" / "nodes_biolink_OrganismTaxon.cypher").read_text(encoding="utf-8")
    assert "biolink:OrganismTaxon" in cypher_text  # label INSIDE statement preserved
    # warning was emitted to WARNINGS.md
    assert (out / "WARNINGS.md").exists()
    warnings_text = (out / "WARNINGS.md").read_text(encoding="utf-8")
    assert "biolink:OrganismTaxon" in warnings_text
    assert "biolink_OrganismTaxon" in warnings_text


def test_relationships_block_unresolved_endpoint_uses_cross_batch_sentinel(tmp_path: Path):
    """Important #3: cross-batch unresolved endpoints emit 'CROSS_BATCH(id=row.X)', not '?' or 'UNKNOWN(...)'."""
    # An edge whose endpoints aren't in this bundle's nodes (cross-batch)
    bundle = ContributionBundle(
        schema_version="1.0",
        schema_config={"prefixes": {}},
        organization_id="org-test",
        nodes=[
            IRNode(
                label="OrganismTaxon",
                id="NCBITaxon:9606",
                properties={"name": "Homo sapiens"},
                provenance={"source": "gtdb", "method": "curated"},
            ),
        ],
        edges=[
            IREdge(
                type="MEMBER_OF",
                from_id="NCBITaxon:9606",
                to_id="NCBITaxon:9999",  # not in bundle.nodes -> cross-batch
                properties={},
                provenance={"source": "gtdb", "method": "curated"},
            ),
        ],
        source=SourceRef(kind="file", path=str(tmp_path / "x.tsv"), sha256="0" * 64),
    )
    out = tmp_path / "bundle"
    serialize_bundle(bundle, out)
    mapping = yaml.safe_load((out / "mapping.yaml").read_text(encoding="utf-8"))
    rels = mapping.get("relationships", [])
    assert len(rels) == 1
    # Either 'from' or 'to' is the unresolved endpoint depending on which is in nodes;
    # only the cross-batch side uses the CROSS_BATCH sentinel.
    assert "CROSS_BATCH" in rels[0]["to"], f"expected 'CROSS_BATCH' sentinel in to-field; got {rels[0]['to']!r}"
    assert "UNKNOWN" not in rels[0]["to"], f"UNKNOWN sentinel should be gone; got {rels[0]['to']!r}"
    assert "?" not in rels[0]["to"], f"'?' sentinel should be gone; got {rels[0]['to']!r}"
