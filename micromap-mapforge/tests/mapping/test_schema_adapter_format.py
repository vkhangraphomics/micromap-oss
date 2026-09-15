"""Confirms mapping.schema.json accepts source.format == 'schema_adapter' (§15.1)."""

import json
from importlib import resources

import jsonschema
import pytest


def _schema() -> dict:
    raw = resources.files("micromap_mapforge.mapping").joinpath(
        "mapping.schema.json"
    ).read_text(encoding="utf-8")
    return json.loads(raw)


def _good_mapping(fmt: str) -> dict:
    return {
        "source": {"name": "gtdb", "format": fmt, "path": "/tmp/gtdb/"},
        "entities": [
            {
                "label": "Taxon",
                "match_on": "name",
                "columns": {"name": "scientific_name"},
            }
        ],
        "relationships": [],
    }


def test_schema_adapter_is_accepted_as_a_source_format():
    jsonschema.validate(_good_mapping("schema_adapter"), _schema())


def test_any_nonempty_format_accepted_after_286_g7():
    """#286 G7 relaxes source.format from a closed enum to any non-empty string —
    the inspector's SourceProfile.format / the format registry is the authority,
    not mapping.schema.json. This mirrors what #74 (slice 4) did for the label
    enum (see test_csv_format_also_accepts_arbitrary_label_after_a1_slice4 below).
    A new inspector's format validates without a schema edit."""
    jsonschema.validate(_good_mapping("biom"), _schema())   # a new inspector's format
    jsonschema.validate(_good_mapping("vcf"), _schema())    # and a future one


def test_empty_format_still_rejected():
    # openness is "any non-empty string" — minLength:1 still catches a missing format
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(_good_mapping(""), _schema())


def test_legacy_csv_format_still_accepted():
    jsonschema.validate(_good_mapping("csv"), _schema())


# ---------------------------------------------------------------------------
# PR #101 Critical #1 — Conditional label-enum relaxation for schema_adapter
# ---------------------------------------------------------------------------

from micromap_mapforge.mapping.validator import (
    validate_mapping,
)


def test_schema_adapter_format_allows_arbitrary_label():
    """Per PR #101 Critical #1: with format='schema_adapter', labels are not enum-restricted."""
    mapping = {
        "source": {"name": "biocypher_gtdb", "format": "schema_adapter", "path": "fake.tsv"},
        "entities": [
            {
                "label": "OrganismTaxon",  # Biolink label, NOT in the strict 9-label enum
                "match_on": "id",
                "columns": {"id": "id", "name": "name"},
            }
        ],
        "relationships": [],
    }
    validate_mapping(mapping)  # must NOT raise


def test_csv_format_also_accepts_arbitrary_label_after_a1_slice4():
    """A1' slice 4 (#74) retired the hardcoded label enum, so the
    strict/permissive distinction by source.format collapses — both
    schema_adapter AND tabular formats now accept any non-empty label.
    The project's schema_config (loaded by the mapper / consumed at
    apply time) is the authority for what's allowed, not
    mapping.schema.json.

    Pre-slice-4 this test asserted the inverse — that `format=csv` kept
    the strict 9-label enum. That distinction is now gone."""
    mapping = {
        "source": {"name": "my_csv", "format": "csv", "path": "fake.csv"},
        "entities": [
            {
                "label": "OrganismTaxon",  # custom label, not in pre-slice-4 enum
                "match_on": "id",
                "columns": {"id": "id"},
            }
        ],
        "relationships": [],
    }
    validate_mapping(mapping)  # must NOT raise post-slice-4
