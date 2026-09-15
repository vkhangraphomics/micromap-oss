from micromap_mapforge.resolve.fuzzy import FuzzyResolver
from micromap_mapforge.resolve.registry import _build_normalized_name_index
from micromap_mapforge.confidence import Confidence

NODES = [
    {"widget_id": "W1", "label": "Acme Widget"},
    {"widget_id": "W2", "label": "Globex Gadget"},
]


def _resolver(normalizer=None, threshold=0.85):
    name_index = _build_normalized_name_index(NODES, "label", normalizer)
    id_index = {"widget_id": {n["widget_id"]: n for n in NODES}}
    return FuzzyResolver(
        label="Widget", name_field="label",
        identifier_fields=["widget_id"], primary_id_field="widget_id",
        identifier_index=id_index, name_index_normalized=name_index,
        normalizer=normalizer, fuzzy_threshold=threshold,
    )


def test_exact_identifier_match():
    r = _resolver()
    out = r.resolve("W1", {"source_field": "widget_id"})
    assert len(out) == 1
    assert out[0].match_type == Confidence.EXTRACTED
    assert out[0].merge_value == "W1"


def test_exact_normalized_name_match():
    r = _resolver()
    out = r.resolve("acme widget", {"source_field": "label"})
    assert len(out) == 1
    assert out[0].match_type == Confidence.INFERRED
    assert out[0].merge_value == "W1"


def test_bounded_fuzzy_name_match():
    r = _resolver(threshold=0.80)
    out = r.resolve("Acme Widgets", {"source_field": "label"})
    assert out and out[0].merge_value == "W1"
    assert out[0].score >= 0.80


def test_below_threshold_returns_empty():
    r = _resolver(threshold=0.95)
    assert r.resolve("totally different", {"source_field": "label"}) == []


def test_normalized_index_keys_use_normalizer():
    idx = _build_normalized_name_index(NODES, "label", None)
    assert "acme widget" in idx
