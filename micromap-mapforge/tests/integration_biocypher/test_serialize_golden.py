"""Golden test for the BioCypher serializer.

Per §15.3 we assert MapForge emit-contract correctness, NOT byte-equivalence
with the #80 spike output. The fixture is a 3-node / 1-edge synthetic GTDB
slice authored from the runner-on-fixture, not copied from the spike.
"""

from pathlib import Path

import yaml

from micromap_mapforge.confidence import Confidence
from micromap_mapforge.integration.biocypher.ir import (
    ContributionBundle,
    IREdge,
    IRNode,
    SourceRef,
)
from micromap_mapforge.integration.biocypher.serialize import serialize_bundle


HERE = Path(__file__).parent
GOLDEN = HERE / "golden" / "gtdb_minimal"


def _bundle() -> ContributionBundle:
    nodes = [
        IRNode(
            label="OrganismTaxon",
            id="NCBITaxon:9606",
            properties={"name": "Homo sapiens"},
            provenance={"source": "gtdb", "method": "curated"},
            confidence=Confidence.EXTRACTED,
        ),
        IRNode(
            label="OrganismTaxon",
            id="NCBITaxon:9605",
            properties={"name": "Homo"},
            provenance={"source": "gtdb", "method": "curated"},
            confidence=Confidence.EXTRACTED,
        ),
    ]
    edges = [
        IREdge(
            type="MEMBER_OF",
            from_id="NCBITaxon:9606",
            to_id="NCBITaxon:9605",
            properties={"evidence": "ncbi"},
            provenance={"source": "gtdb", "method": "curated"},
            confidence=Confidence.EXTRACTED,
        )
    ]
    return ContributionBundle(
        schema_version="1.0",
        schema_config={"name": "biolink-mini", "prefixes": {"NCBITaxon": "x"}},
        organization_id="org-test",
        nodes=nodes,
        edges=edges,
        source=SourceRef(kind="file", path="/tmp/gtdb.tsv", sha256="0" * 64),
    )


def _normalize(text: str) -> str:
    """Strip trailing whitespace per line; ignore blank-line-only differences."""
    return "\n".join(line.rstrip() for line in text.splitlines() if line.strip())


def test_golden_mapping_yaml_matches(tmp_path: Path):
    serialize_bundle(_bundle(), out_dir=tmp_path)
    generated = yaml.safe_load((tmp_path / "mapping.yaml").read_text(encoding="utf-8"))
    expected  = yaml.safe_load((GOLDEN / "mapping.yaml").read_text(encoding="utf-8"))
    # mapping.yaml comparison is structural, not textual.
    assert generated == expected


def test_golden_nodes_cypher_matches(tmp_path: Path):
    serialize_bundle(_bundle(), out_dir=tmp_path)
    gen = (tmp_path / "cypher" / "nodes_OrganismTaxon.cypher").read_text(encoding="utf-8")
    exp = (GOLDEN / "cypher" / "nodes_OrganismTaxon.cypher").read_text(encoding="utf-8")
    assert _normalize(gen) == _normalize(exp)


def test_golden_rels_cypher_matches(tmp_path: Path):
    serialize_bundle(_bundle(), out_dir=tmp_path)
    gen = (tmp_path / "cypher" / "rels_MEMBER_OF.cypher").read_text(encoding="utf-8")
    exp = (GOLDEN / "cypher" / "rels_MEMBER_OF.cypher").read_text(encoding="utf-8")
    assert _normalize(gen) == _normalize(exp)
