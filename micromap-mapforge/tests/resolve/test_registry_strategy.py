from unittest.mock import MagicMock

from micromap_mapforge.resolve.registry import build_resolvers, _strategy_for
from micromap_mapforge.resolve.fuzzy import FuzzyResolver
from micromap_mapforge.resolve.generic import GenericResolver


def _fake_driver(records_by_label):
    driver = MagicMock(); ctx = MagicMock(); session = MagicMock()
    driver.session.return_value = ctx
    ctx.__enter__.return_value = session
    ctx.__exit__.return_value = False

    def run(cypher, **kw):
        for label, rows in records_by_label.items():
            if f"(n:{label})" in cypher:
                return [dict(r) for r in rows]
        return []

    session.run = run
    return driver


_WIDGET_SCHEMA = {
    "name": "widgets",
    "prefixes": {"W": "https://example/w/"},
    "classes": {
        "Widget": {
            "id_prefixes": ["W"],
            "slots": ["id", "label"],
            "x_mapforge": {
                "primary_id": "widget_id",
                "identifiers": ["widget_id"],
                "name_field": "label",
                "resolver": {"strategy": "fuzzy"},
            },
        },
    },
}


def test_strategy_for_explicit_wins():
    assert _strategy_for({"resolver": {"strategy": "paper"}, "normalizer": "x"}) == "paper"


def test_strategy_for_infers_fuzzy_from_normalizer():
    assert _strategy_for({"normalizer": "normalize_disease_name"}) == "fuzzy"


def test_strategy_for_defaults_generic():
    assert _strategy_for({"normalizer": None}) == "generic"
    assert _strategy_for({}) == "generic"


def test_new_domain_fuzzy_strategy_no_code_edit():
    driver = _fake_driver({"Widget": [{"widget_id": "W1", "label": "Acme Widget"}]})
    resolvers = build_resolvers(driver, database="neo4j", schema_config=_WIDGET_SCHEMA)
    assert isinstance(resolvers["Widget"], FuzzyResolver)
    out = resolvers["Widget"].resolve("acme widget", {"source_field": "label"})
    assert out and out[0].merge_value == "W1"


def test_unknown_strategy_falls_back_to_generic(caplog):
    import logging
    schema = {**_WIDGET_SCHEMA}
    schema["classes"] = {"Widget": {**_WIDGET_SCHEMA["classes"]["Widget"]}}
    schema["classes"]["Widget"]["x_mapforge"] = {
        **_WIDGET_SCHEMA["classes"]["Widget"]["x_mapforge"],
        "resolver": {"strategy": "no_such_strategy"},
    }
    driver = _fake_driver({"Widget": [{"widget_id": "W1", "label": "Acme"}]})
    with caplog.at_level(logging.WARNING):
        resolvers = build_resolvers(driver, database="neo4j", schema_config=schema)
    assert isinstance(resolvers["Widget"], GenericResolver)
    assert "no_such_strategy" in caplog.text
