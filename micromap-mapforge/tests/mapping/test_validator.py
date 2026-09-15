# micromap-mapforge/tests/mapping/test_validator.py
from pathlib import Path

import pytest

from micromap_mapforge.mapping.validator import (
    MappingValidationError,
    validate_mapping,
    load_and_validate,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_valid_mapping_passes():
    result = load_and_validate(FIXTURES / "valid_mapping.yaml")
    assert result["source"]["name"] == "partner_gut_study_2026"
    assert result["entities"][0]["label"] == "Taxon"


def test_invalid_mapping_raises():
    with pytest.raises(MappingValidationError) as excinfo:
        load_and_validate(FIXTURES / "invalid_mapping.yaml")
    msg = str(excinfo.value)
    assert "source" in msg or "entities" in msg


def test_source_ref_column_is_allowed():
    """3b-2 / #126: source.ref_column is an optional field consumed by the
    tabular path's IR adapter (tabular_to_ir copies the per-row value into
    provenance.ref). Without this entry in mapping.schema.json the source
    block's additionalProperties:false would reject the field with a
    confusing 'unknown property' error at validate_mapping time."""
    mapping = {
        "source": {"name": "x", "format": "csv", "path": "x.csv", "ref_column": "pmid"},
        "entities": [
            {"label": "Taxon", "match_on": "ncbi_tax_id",
             "columns": {"ncbi_tax_id": "tax_id"}},
        ],
        "relationships": [],
    }
    validate_mapping(mapping)  # must not raise


def test_bad_entity_shape_rejected():
    """A1' slice 4 (#74) relaxed the strict label enum — labels are now any
    non-empty string, with the project's schema_config the authority for
    what's allowed (enforced upstream by the mapper's _allowed_labels).
    The schema still rejects structurally invalid entities; this test pins
    a missing required field (``columns``)."""
    mapping = {
        "source": {"name": "x", "format": "tsv", "path": "x.tsv"},
        "entities": [
            {"label": "Anything", "match_on": "id"},  # columns missing
        ],
        "relationships": [],
    }
    with pytest.raises(MappingValidationError):
        validate_mapping(mapping)


def test_custom_label_accepted_after_a1_slice4():
    """Regression guard for slice 4: a custom (non-default) entity label
    no longer fails validation. Before slice 4 this raised because the
    strict label enum was hardcoded to MicroMap's default node types."""
    mapping = {
        "source": {"name": "x", "format": "tsv", "path": "x.tsv"},
        "entities": [
            {"label": "MyCustomLabel", "match_on": "id", "columns": {"id": "col"}},
        ],
        "relationships": [],
    }
    validate_mapping(mapping)  # must not raise


def test_bad_confidence_rejected():
    mapping = {
        "source": {"name": "x", "format": "tsv", "path": "x.tsv"},
        "entities": [
            {
                "label": "Taxon",
                "match_on": "ncbi_tax_id",
                "columns": {"ncbi_tax_id": "tax_id"},
                "confidence": "MAYBE",
            }
        ],
        "relationships": [],
    }
    with pytest.raises(MappingValidationError):
        validate_mapping(mapping)


# ---------------------------------------------------------------------------
# E3 (#78): source attribution fields in mapping.yaml::source
# ---------------------------------------------------------------------------


def _minimal_mapping_with_source(**source_extras) -> dict:
    """Minimal valid mapping with a source block plus any e3 extras.

    `entities` and `relationships` are kept at the minimum to satisfy the
    structural part of the schema; tests focus on the source block.
    """
    source = {
        "name": "fixture",
        "format": "csv",
        "path": "/tmp/fixture.csv",
    }
    source.update(source_extras)
    return {
        "source": source,
        "entities": [{
            "label": "Widget", "match_on": "id",
            "columns": {"id": "id_col"},
        }],
        "relationships": [],
    }


def test_source_block_accepts_license():
    """A schema-conformant SPDX-style license string passes validation."""
    validate_mapping(_minimal_mapping_with_source(license="CC-BY-4.0"))


def test_source_block_accepts_doi():
    """A valid DOI passes the ^10\\.\\d{4,}/[^\\s]+$ pattern."""
    validate_mapping(_minimal_mapping_with_source(doi="10.1093/nar/gkx1132"))


def test_source_block_accepts_url():
    """A valid https URL passes format: uri."""
    validate_mapping(_minimal_mapping_with_source(url="https://example.com/data"))


def test_source_block_accepts_email_contact():
    """A valid email passes format: email."""
    validate_mapping(_minimal_mapping_with_source(contact="help@example.com"))


def test_source_block_accepts_iso_accessed_at():
    """ISO YYYY-MM-DD passes the date pattern."""
    validate_mapping(_minimal_mapping_with_source(accessed_at="2026-06-06"))


def test_source_block_rejects_malformed_doi():
    """A string that doesn't match the DOI pattern raises with field-named message."""
    with pytest.raises(MappingValidationError) as exc_info:
        validate_mapping(_minimal_mapping_with_source(doi="not-a-doi"))
    msg = str(exc_info.value)
    assert "doi" in msg


def test_source_block_rejects_malformed_url():
    """A malformed URL raises -- proves FormatChecker is wired in.

    Without FormatChecker, format: uri is advisory and `not-a-url` would
    pass. This test pins the validator construction change in
    mapping/validator.py.
    """
    with pytest.raises(MappingValidationError) as exc_info:
        validate_mapping(_minimal_mapping_with_source(url="not-a-url"))
    msg = str(exc_info.value)
    assert "url" in msg


def test_source_block_rejects_malformed_email_contact():
    """A string missing @ raises format: email check."""
    with pytest.raises(MappingValidationError) as exc_info:
        validate_mapping(_minimal_mapping_with_source(contact="missing-at-sign.com"))
    msg = str(exc_info.value)
    assert "contact" in msg


def test_source_block_rejects_us_date_accessed_at():
    """US-format date like 06/06/2026 fails the YYYY-MM-DD pattern."""
    with pytest.raises(MappingValidationError) as exc_info:
        validate_mapping(_minimal_mapping_with_source(accessed_at="06/06/2026"))
    msg = str(exc_info.value)
    assert "accessed_at" in msg


def test_source_block_all_e3_fields_absent_validates():
    """Bundle without any e3 fields validates -- back-compat with pre-E3 bundles.

    The minimal mapping has only name/format/path. No e3 field is required.
    """
    validate_mapping(_minimal_mapping_with_source())
