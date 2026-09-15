"""IR dataclass construction tests (no JSON-schema in this task)."""

from micromap_mapforge.integration.biocypher.ir import (
    ContributionBundle,
    IREdge,
    IRNode,
    SourceRef,
)
from micromap_mapforge.confidence import Confidence


def _node(label="OrganismTaxon", _id="NCBITaxon:9606") -> IRNode:
    return IRNode(
        label=label,
        id=_id,
        properties={"name": "Homo sapiens"},
        provenance={"source": "gtdb", "method": "curated"},
        confidence=Confidence.EXTRACTED,
        tier="execution",
    )


def test_contribution_bundle_minimal_fields():
    bundle = ContributionBundle(
        schema_version="1.0",
        schema_config={"prefixes": {"NCBITaxon": "http://purl.obolibrary.org/obo/NCBITaxon_"}},
        organization_id="org-test",
        nodes=[_node()],
        edges=[],
        source=SourceRef(kind="file", path="/tmp/gtdb.tsv", sha256="0" * 64),
    )
    assert bundle.organization_id == "org-test"
    assert bundle.nodes[0].label == "OrganismTaxon"
    assert bundle.source.kind == "file"


def test_iredge_required_fields_and_optional_tier():
    edge = IREdge(
        type="MEMBER_OF",
        from_id="NCBITaxon:9606",
        to_id="NCBITaxon:9605",
        properties={"evidence": "ncbi"},
        provenance={"source": "ncbi", "method": "curated"},
        confidence=Confidence.EXTRACTED,
        tier=None,
    )
    assert edge.tier is None
    assert edge.properties["evidence"] == "ncbi"


def test_source_ref_archive_variant():
    ref = SourceRef(kind="archive", archive_path="/tmp/gtdb.tar.gz", sha256="a" * 64)
    assert ref.kind == "archive"
    assert ref.path is None
    assert ref.archive_path == "/tmp/gtdb.tar.gz"


# ---------------------------------------------------------------------------
# Task 1.2 — JSON-schema validation tests
# ---------------------------------------------------------------------------

import pytest

from micromap_mapforge.integration.biocypher.ir import (
    IRValidationError,
    validate_bundle_dict,
)


def _good_bundle_dict() -> dict:
    return {
        "schema_version": "1.0",
        "schema_config": {"prefixes": {"NCBITaxon": "http://example/"}},
        "organization_id": "org-test",
        "nodes": [
            {
                "label": "OrganismTaxon",
                "id": "NCBITaxon:9606",
                "properties": {"name": "Homo sapiens"},
                "provenance": {"source": "gtdb", "method": "curated"},
                "confidence": "EXTRACTED",
            }
        ],
        "edges": [],
        "source": {"kind": "file", "path": "/tmp/x.tsv", "sha256": "f" * 64},
    }


def test_validate_bundle_dict_accepts_good_payload():
    validate_bundle_dict(_good_bundle_dict())


def test_validate_bundle_dict_rejects_missing_schema_config():
    bad = _good_bundle_dict()
    del bad["schema_config"]
    with pytest.raises(IRValidationError, match="schema_config"):
        validate_bundle_dict(bad)


def test_validate_bundle_dict_rejects_unknown_confidence_value():
    bad = _good_bundle_dict()
    bad["nodes"][0]["confidence"] = "MAYBE"
    with pytest.raises(IRValidationError):
        validate_bundle_dict(bad)


def test_validate_bundle_dict_accepts_archive_source_ref():
    bundle = _good_bundle_dict()
    bundle["source"] = {"kind": "archive", "archive_path": "/tmp/x.tar.gz", "sha256": "a" * 64}
    validate_bundle_dict(bundle)


def test_validate_bundle_dict_rejects_bad_sha256():
    """Regression guard for the schema's sha256 pattern (review #12 follow-up)."""
    too_short = _good_bundle_dict()
    too_short["source"]["sha256"] = "a" * 63
    with pytest.raises(IRValidationError):
        validate_bundle_dict(too_short)

    uppercase = _good_bundle_dict()
    uppercase["source"]["sha256"] = "F" * 64
    with pytest.raises(IRValidationError):
        validate_bundle_dict(uppercase)

    non_hex = _good_bundle_dict()
    non_hex["source"]["sha256"] = "g" * 64
    with pytest.raises(IRValidationError):
        validate_bundle_dict(non_hex)


# ---------------------------------------------------------------------------
# Task 1.3 — Schema-agnostic IR proof (Biolink + custom LinkML)
# ---------------------------------------------------------------------------

BIOLINK_SCHEMA_CONFIG = {
    "name": "biolink-mini",
    "prefixes": {
        "biolink":   "https://w3id.org/biolink/vocab/",
        "NCBITaxon": "http://purl.obolibrary.org/obo/NCBITaxon_",
    },
    "classes": {
        "OrganismTaxon": {
            "is_a": "NamedThing",
            "id_prefixes": ["NCBITaxon"],
            "slots": ["id", "name"],
        }
    },
}

# Custom (non-Biolink) — e.g. a financial/program tier domain.
CUSTOM_LINKML_SCHEMA_CONFIG = {
    "name": "graphomics-portfolio",
    "prefixes": {"GRX": "https://graphomics.com/portfolio/"},
    "classes": {
        "Program": {
            "id_prefixes": ["GRX"],
            "slots": ["id", "name", "annual_budget"],
        },
        "FundingRound": {
            "id_prefixes": ["GRX"],
            "slots": ["id", "amount_usd", "closed_on"],
        },
    },
}


def test_biolink_schema_config_validates():
    bundle = _good_bundle_dict()
    bundle["schema_config"] = BIOLINK_SCHEMA_CONFIG
    validate_bundle_dict(bundle)


def test_custom_linkml_schema_config_validates():
    bundle = _good_bundle_dict()
    bundle["schema_config"] = CUSTOM_LINKML_SCHEMA_CONFIG
    bundle["nodes"] = [
        {
            "label": "Program",
            "id": "GRX:001",
            "properties": {"name": "Aging-Microbiome", "annual_budget": 1_500_000},
            "provenance": {"source": "internal-grants-db", "method": "curated"},
        }
    ]
    validate_bundle_dict(bundle)


# ---------------------------------------------------------------------------
# PR #101 follow-ups
# ---------------------------------------------------------------------------

def test_source_ref_post_init_rejects_uppercase_hex():
    """Important #6: dataclass guard now matches JSON-schema ^[0-9a-f]{64}$."""
    with pytest.raises(ValueError, match="64-char lowercase hex"):
        SourceRef(kind="file", path="/tmp/x", sha256="F" * 64)


def test_source_ref_post_init_rejects_non_hex():
    """Important #6: 'g' is not hex; must fail at construction, not at validate_bundle_dict."""
    with pytest.raises(ValueError, match="64-char lowercase hex"):
        SourceRef(kind="file", path="/tmp/x", sha256="g" * 64)


def test_source_ref_post_init_accepts_valid_hex():
    """Sanity: lowercase 64-char hex passes the new guard."""
    ref = SourceRef(kind="file", path="/tmp/x", sha256="a1b2c3d4" * 8)  # 64 chars
    assert ref.sha256 == "a1b2c3d4" * 8


def test_bundle_to_schema_dict_strips_sourceref_none_fields():
    """Important #5: helper produces a JSON-schema-valid payload for a file-kind SourceRef."""
    from micromap_mapforge.integration.biocypher.ir import bundle_to_schema_dict
    bundle = ContributionBundle(
        schema_version="1.0",
        schema_config={"prefixes": {}},
        organization_id="org-test",
        nodes=[
            IRNode(
                label="Taxon",
                id="NCBITaxon:9606",
                properties={"name": "Homo sapiens"},
                provenance={"source": "test"},
            ),
        ],
        edges=[],
        source=SourceRef(kind="file", path="/tmp/x.tsv", sha256="0" * 64),
    )
    payload = bundle_to_schema_dict(bundle)
    # archive_path was None on a file-kind SourceRef; helper must strip it
    assert "archive_path" not in payload["source"]
    assert payload["source"]["path"] == "/tmp/x.tsv"
    assert payload["source"]["kind"] == "file"
    # the result MUST validate against the IR schema
    validate_bundle_dict(payload)


def test_bundle_to_schema_dict_archive_kind_preserves_archive_path():
    """archive-kind SourceRefs strip the unused path field; archive_path stays."""
    from micromap_mapforge.integration.biocypher.ir import bundle_to_schema_dict
    bundle = ContributionBundle(
        schema_version="1.0",
        schema_config={"prefixes": {}},
        organization_id="org-test",
        nodes=[
            IRNode(
                label="Taxon",
                id="NCBITaxon:9606",
                properties={},
                provenance={"source": "test"},
            ),
        ],
        edges=[],
        source=SourceRef(kind="archive", archive_path="/tmp/x.tar.gz", sha256="0" * 64),
    )
    payload = bundle_to_schema_dict(bundle)
    assert "path" not in payload["source"]
    assert payload["source"]["archive_path"] == "/tmp/x.tar.gz"
    validate_bundle_dict(payload)
