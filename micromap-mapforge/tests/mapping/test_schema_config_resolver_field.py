"""#202: x_mapforge.resolver is pulled onto each node_def so the resolver
registry can drive strategy selection from config."""

from micromap_mapforge.mapping.schema_config import load_ontology_from_schema_config


def test_resolver_block_is_pulled_onto_node_def():
    schema = {
        "name": "tiny",
        "prefixes": {"X": "https://example/x/"},
        "classes": {
            "Widget": {
                "id_prefixes": ["X"],
                "slots": ["id", "label"],
                "x_mapforge": {
                    "primary_id": "widget_id",
                    "identifiers": ["widget_id"],
                    "name_field": "label",
                    "resolver": {"strategy": "fuzzy", "threshold": 0.9},
                },
            },
        },
    }
    ont = load_ontology_from_schema_config(schema)
    assert ont.nodes["Widget"]["resolver"] == {"strategy": "fuzzy", "threshold": 0.9}


def test_missing_resolver_block_is_none():
    schema = {
        "name": "tiny",
        "prefixes": {"X": "https://example/x/"},
        "classes": {
            "Widget": {
                "id_prefixes": ["X"],
                "slots": ["id"],
                "x_mapforge": {"primary_id": "widget_id", "identifiers": ["widget_id"]},
            }
        },
    }
    ont = load_ontology_from_schema_config(schema)
    assert ont.nodes["Widget"]["resolver"] is None
