"""Resolver registry consumes schema_config (Theme A1' slice 3 / #74, part of #135).

After this slice, ``build_resolvers`` accepts a ``schema_config`` parameter
and uses it to discover labels + identifier fields + name_field, in place
of the previous direct read of ``mapping/ontology.yaml``. ``schema_config=None``
falls back to the package-baked default so existing callers keep working.

Pins:
  - The default-fallback path enumerates the same set of labels as
    pre-slice-3 (regression guard via labels_of_interest filter).
  - An explicit schema_config replaces the default — labels outside the
    custom schema are not preloaded.
  - The Neo4j projection comes from the schema_config's x_mapforge fields
    (identifiers + name_field), not from ontology.yaml.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from micromap_mapforge.mapping.schema_config import (
    Ontology,
    default_schema_config,
    load_ontology_from_schema_config,
)
from micromap_mapforge.resolve.registry import (
    _projection_fields,
    build_resolvers,
)


# ---------------------------------------------------------------------------
# _projection_fields uses the same shape produced by schema_config
# ---------------------------------------------------------------------------


def test_projection_fields_returns_identifiers_plus_name_field():
    """_projection_fields reads identifiers + name_field from the Ontology
    shape — same as before, just produced from schema_config now."""
    schema = {
        "name": "tiny",
        "prefixes": {"X": "https://example/x/"},
        "classes": {
            "Foo": {
                "id_prefixes": ["X"],
                "slots": ["id", "name"],
                "x_mapforge": {
                    "primary_id": "foo_id",
                    "identifiers": ["foo_id", "alt_id"],
                    "name_field": "title",
                },
            },
        },
    }
    ontology = load_ontology_from_schema_config(schema)
    fields = _projection_fields("Foo", _ontology_to_registry_dict(ontology))
    assert fields == ["foo_id", "alt_id", "title"]


def test_projection_fields_dedupes_when_name_field_is_an_identifier():
    """Disease has name_normalized in BOTH identifiers AND as name_field
    (well, primary_id) in the default. The projection must not duplicate."""
    schema = {
        "name": "tiny",
        "prefixes": {"X": "https://example/x/"},
        "classes": {
            "Foo": {
                "id_prefixes": ["X"],
                "slots": ["id", "name"],
                "x_mapforge": {
                    "primary_id": "name_normalized",
                    "identifiers": ["name_normalized", "alt_id"],
                    "name_field": "name_normalized",  # also an identifier
                },
            },
        },
    }
    ontology = load_ontology_from_schema_config(schema)
    fields = _projection_fields("Foo", _ontology_to_registry_dict(ontology))
    assert fields == ["name_normalized", "alt_id"]


def _ontology_to_registry_dict(ontology: Ontology) -> dict:
    """Bridge: registry's _projection_fields takes the legacy dict shape
    (`{nodes: {label: {...}}}`); Ontology dataclass has `.nodes` directly.
    Tests use both shapes."""
    return {"nodes": ontology.nodes}


# ---------------------------------------------------------------------------
# build_resolvers with schema_config
# ---------------------------------------------------------------------------


def _capture_driver():
    """Fake Neo4j driver that returns empty rows for every query."""
    driver = MagicMock()
    session_ctx = MagicMock()
    session = MagicMock()
    driver.session.return_value = session_ctx
    session_ctx.__enter__ = MagicMock(return_value=session)
    session_ctx.__exit__ = MagicMock(return_value=False)
    result = MagicMock()
    result.data.return_value = []
    result.__iter__ = lambda self: iter([])
    session.run = MagicMock(return_value=result)
    return driver, session.run


def test_build_resolvers_default_fallback_enumerates_default_labels():
    """build_resolvers() with no schema_config uses the package default
    (microbiome). The full set of default labels gets a resolver."""
    driver, _ = _capture_driver()
    resolvers = build_resolvers(driver, database="x")
    expected_node_labels = {
        label for label, cls in default_schema_config()["classes"].items()
        if cls.get("is_a") != "association"
    }
    assert set(resolvers.keys()) == expected_node_labels


def test_build_resolvers_with_explicit_schema_config_replaces_default():
    """Custom schema_config — only its classes get resolvers."""
    schema = {
        "name": "custom",
        "prefixes": {"X": "https://example/x/"},
        "classes": {
            "Taxon": {
                "id_prefixes": ["X"],
                "slots": ["id"],
                "x_mapforge": {
                    "primary_id": "taxon_id",
                    "identifiers": ["taxon_id"],
                    "name_field": "name",
                },
            },
        },
    }
    driver, _ = _capture_driver()
    resolvers = build_resolvers(driver, database="x", schema_config=schema)
    assert set(resolvers.keys()) == {"Taxon"}
    # No Disease/Metabolite/etc. — they're only in the default.


def test_build_resolvers_labels_of_interest_intersected_with_schema_config():
    """labels_of_interest (#117 filter) still works — it intersects with
    whichever schema_config is in play."""
    schema = {
        "name": "custom",
        "prefixes": {"X": "https://example/x/"},
        "classes": {
            "Taxon": {"slots": ["id"], "x_mapforge": {
                "primary_id": "taxon_id", "identifiers": ["taxon_id"], "name_field": "name"}},
            "Disease": {"slots": ["id"], "x_mapforge": {
                "primary_id": "disease_id", "identifiers": ["disease_id"], "name_field": "name"}},
            "Metabolite": {"slots": ["id"], "x_mapforge": {
                "primary_id": "metabolite_id", "identifiers": ["metabolite_id"], "name_field": "name"}},
        },
    }
    driver, _ = _capture_driver()
    resolvers = build_resolvers(
        driver, database="x", schema_config=schema,
        labels_of_interest={"Taxon", "Metabolite", "NotInSchema"},
    )
    # Disease excluded by labels_of_interest; NotInSchema excluded by intersection.
    assert set(resolvers.keys()) == {"Taxon", "Metabolite"}


def test_build_resolvers_skips_association_classes():
    """Classes with is_a: association are relationships, not node labels —
    no resolver gets built for them even though they're in the schema."""
    schema = {
        "name": "with-edges",
        "prefixes": {"X": "https://example/x/"},
        "classes": {
            "Taxon": {"slots": ["id"], "x_mapforge": {
                "primary_id": "taxon_id", "identifiers": ["taxon_id"], "name_field": "name"}},
            "ASSOCIATES": {
                "is_a": "association",
                "subject": "Taxon",
                "object": "Taxon",
                "x_mapforge": {"properties": [], "status": "populated"},
            },
        },
    }
    driver, _ = _capture_driver()
    resolvers = build_resolvers(driver, database="x", schema_config=schema)
    assert "ASSOCIATES" not in resolvers
    assert "Taxon" in resolvers
