"""Mapper-with-schema_config tests (Theme A1' slice 2 / #74, part of #135).

After this slice, ``draft_heuristic_mapping`` and ``propose_mapping`` consume
a project's ``schema_config`` (LinkML + ``x_mapforge:`` extensions) instead of
the package-baked ``mapping/ontology.yaml``. ``schema_config=None`` falls back
to the package default so existing callers keep working without changes.

These tests pin:
  - The default-fallback path produces the same mapping shape as before.
  - An explicit schema_config replaces the ontology fully — custom classes
    flow through, default classes don't leak in.
  - ``x_mapforge.common_columns_hint`` / ``identifiers`` / ``name_field`` /
    ``normalizer`` are consumed correctly from the new format.
  - LinkML ``classes`` keys with ``is_a: association`` are treated as
    relationships, not as candidate node labels.
  - The mapper still validates against ``mapping.schema.json`` — schema_config
    classes not in the strict-enum are silently skipped, matching the
    pre-slice-2 ``_allowed_labels`` intersection behavior. (Retiring the
    strict enum is a later slice.)
"""

from __future__ import annotations

from typing import Any


from micromap_mapforge.inspect.types import ColumnProfile, SourceProfile
from micromap_mapforge.mapping.mapper import (
    Ontology,
    draft_heuristic_mapping,
    load_ontology_from_schema_config,
)


# ---------------------------------------------------------------------------
# load_ontology_from_schema_config — schema_config → Ontology conversion
# ---------------------------------------------------------------------------


def _node_class(**x_mapforge: Any) -> dict:
    return {
        "id_prefixes": ["X"],
        "slots": ["id", "name"],
        "x_mapforge": x_mapforge,
    }


def _rel_class(subject: str, obj: str, **x_mapforge: Any) -> dict:
    return {
        "is_a": "association",
        "subject": subject,
        "object": obj,
        "x_mapforge": x_mapforge,
    }


def test_load_ontology_from_schema_config_extracts_nodes_and_relationships():
    schema = {
        "name": "tiny",
        "prefixes": {"X": "https://example/x/"},
        "classes": {
            "Foo": _node_class(primary_id="foo_id", identifiers=["foo_id"]),
            "Bar": _node_class(primary_id="bar_id", identifiers=["bar_id"]),
            "RELATES_TO": _rel_class("Foo", "Bar", properties=["weight"], status="populated"),
        },
    }
    ont = load_ontology_from_schema_config(schema)
    assert isinstance(ont, Ontology)
    assert set(ont.nodes.keys()) == {"Foo", "Bar"}
    assert set(ont.relationships.keys()) == {"RELATES_TO"}
    assert ont.relationships["RELATES_TO"]["from"] == "Foo"
    assert ont.relationships["RELATES_TO"]["to"] == "Bar"
    assert ont.relationships["RELATES_TO"]["properties"] == ["weight"]
    assert ont.relationships["RELATES_TO"]["status"] == "populated"


def test_load_ontology_pulls_x_mapforge_hints_onto_node_dict():
    schema = {
        "name": "tiny",
        "prefixes": {"X": "https://example/x/"},
        "classes": {
            "Foo": _node_class(
                primary_id="foo_id",
                identifiers=["foo_id", "alt_id"],
                name_field="title",
                common_columns_hint=["foo", "alpha"],
                normalizer="lowercase_strip",
            ),
        },
    }
    ont = load_ontology_from_schema_config(schema)
    foo = ont.nodes["Foo"]
    assert foo["primary_id"] == "foo_id"
    assert foo["identifiers"] == ["foo_id", "alt_id"]
    assert foo["name_field"] == "title"
    assert foo["common_columns_hint"] == ["foo", "alpha"]
    assert foo["normalizer"] == "lowercase_strip"


def test_load_ontology_handles_class_with_no_x_mapforge_extension():
    """A class without x_mapforge: still loads — defaults to empty hints.
    Mapper will then fail to find any columns and skip that label."""
    schema = {
        "name": "tiny",
        "prefixes": {"X": "https://example/x/"},
        "classes": {
            "Plain": {"id_prefixes": ["X"], "slots": ["id"]},  # no x_mapforge
        },
    }
    ont = load_ontology_from_schema_config(schema)
    assert "Plain" in ont.nodes
    assert ont.nodes["Plain"]["identifiers"] == []
    assert ont.nodes["Plain"]["common_columns_hint"] == []


def test_load_ontology_treats_is_a_association_as_relationship_only():
    """A class with is_a: association is NOT also a candidate node label."""
    schema = {
        "name": "tiny",
        "prefixes": {"X": "https://example/x/"},
        "classes": {
            "Foo": _node_class(primary_id="foo_id"),
            "ASSOCIATES": _rel_class("Foo", "Foo"),
        },
    }
    ont = load_ontology_from_schema_config(schema)
    assert "ASSOCIATES" not in ont.nodes
    assert "ASSOCIATES" in ont.relationships


# ---------------------------------------------------------------------------
# draft_heuristic_mapping with schema_config
# ---------------------------------------------------------------------------


def _profile_with_columns(*column_names: str) -> SourceProfile:
    return SourceProfile(
        path="/tmp/test.tsv",
        format="tsv",
        row_count_estimate=10,
        columns=[
            ColumnProfile(name=n, inferred_type="string",
                          null_rate=0.0, distinct_count=10, samples=[])
            for n in column_names
        ],
    )


def test_default_fallback_produces_same_mapping_as_old_ontology_yaml():
    """Backward-compat: draft_heuristic_mapping(profile) with no schema_config
    arg falls back to the package-baked default, which is the port of
    ontology.yaml. The resulting mapping must include the same Taxon entry
    as the pre-slice-2 behavior (regression guard).

    Column ``ncbi_tax_id`` exercises the identifier-match path
    (_find_column checks ``ontology_field in column_name``, so column
    ``ncbi_tax_id`` matches identifier ``ncbi_tax_id`` exactly)."""
    profile = _profile_with_columns("ncbi_tax_id", "organism")
    mapping = draft_heuristic_mapping(profile)  # no schema_config arg
    taxon_entries = [e for e in mapping["entities"] if e["label"] == "Taxon"]
    assert len(taxon_entries) == 1
    assert taxon_entries[0]["match_on"] == "ncbi_tax_id"
    assert "ncbi_tax_id" in taxon_entries[0]["columns"].values()


def test_explicit_schema_config_replaces_default_fully():
    """When passed an explicit schema_config, the mapper does NOT mix in
    the default — only the explicit classes are candidate labels."""
    # NOTE: this custom class won't match mapping.schema.json's strict label
    # enum, so the mapping will skip it. Test uses a schema_config whose
    # classes ARE in the strict enum (Taxon, Drug) but with different
    # column hints than the default.
    schema = {
        "name": "custom-overlap",
        "prefixes": {"X": "https://example/x/"},
        "classes": {
            "Taxon": _node_class(
                primary_id="my_taxon_id",
                identifiers=["my_taxon_id"],
                name_field="my_name",
                common_columns_hint=["weird_column_name_only_used_in_this_test"],
            ),
        },
    }
    profile = _profile_with_columns("my_taxon_id", "weird_column_name_only_used_in_this_test")
    mapping = draft_heuristic_mapping(profile, schema_config=schema)
    taxon = next(e for e in mapping["entities"] if e["label"] == "Taxon")
    # match_on came from the custom schema_config, not from the default's
    # 'ncbi_tax_id'.
    assert taxon["match_on"] == "my_taxon_id"
    # No 'Disease' entity — it's only in the default schema_config.
    assert not any(e["label"] == "Disease" for e in mapping["entities"])


def test_schema_config_x_mapforge_normalizer_flows_to_mapping():
    """Verify the normalizer flows from schema_config.x_mapforge to the
    emitted mapping entry — the resolver depends on this field."""
    schema = {
        "name": "norm-test",
        "prefixes": {"X": "https://example/x/"},
        "classes": {
            "Disease": _node_class(
                primary_id="name_normalized",
                identifiers=["name_normalized", "disease_id"],
                name_field="name",
                common_columns_hint=["disease", "condition"],
                normalizer="normalize_disease_name",
            ),
        },
    }
    profile = _profile_with_columns("disease", "name_normalized")
    mapping = draft_heuristic_mapping(profile, schema_config=schema)
    disease = next(e for e in mapping["entities"] if e["label"] == "Disease")
    assert disease.get("normalizer") == "normalize_disease_name"


def test_schema_config_class_outside_default_labels_now_emits_after_slice4():
    """A1' slice 4 (#74) retired the hardcoded label enum in
    ``mapping.schema.json``. Custom schema_config classes that previously
    got silently filtered by ``_allowed_labels`` are now legitimate
    targets for the mapper — the project's schema_config is the sole
    authority for allowed labels.

    Regression guard: before slice 4 this test asserted the inverse
    (validation raised because zero entities passed the strict enum
    intersection). After slice 4 the mapper emits the custom label and
    validation accepts it."""
    schema = {
        "name": "custom-domain",
        "prefixes": {"X": "https://example/x/"},
        "classes": {
            "CustomDomainEntity": _node_class(
                primary_id="some_id", identifiers=["some_id"],
                name_field="title",
                common_columns_hint=["custom_col"],
            ),
        },
    }
    profile = _profile_with_columns("some_id", "title")
    mapping = draft_heuristic_mapping(profile, schema_config=schema)
    entities = [e for e in mapping["entities"] if e["label"] == "CustomDomainEntity"]
    assert len(entities) == 1
    assert entities[0]["match_on"] == "some_id"


# ---------------------------------------------------------------------------
# propose_mapping with schema_config
# ---------------------------------------------------------------------------


def test_propose_mapping_passes_schema_config_to_system_prompt():
    """The LLM path's system cache block must reflect the custom
    schema_config, not the package default — so the LLM sees the actual
    labels the project allows."""
    from unittest.mock import MagicMock

    from micromap_mapforge.mapping.mapper import propose_mapping

    schema = {
        "name": "custom-llm-test",
        "prefixes": {"X": "https://example/x/"},
        "classes": {
            "Taxon": _node_class(primary_id="my_taxon_id",
                                 identifiers=["my_taxon_id"]),
        },
    }
    # Mock the LLM client to return a valid-shaped mapping.
    fake_response = MagicMock(content=[MagicMock(text=(
        "source:\n  name: x\n  format: tsv\n  path: /tmp/x.tsv\n"
        "entities:\n"
        "  - label: Taxon\n    match_on: my_taxon_id\n"
        "    columns:\n      my_taxon_id: tax_id\n    confidence: INFERRED\n"
        "relationships: []\n"
    ))])
    fake_client = MagicMock()
    fake_client.messages.create = MagicMock(return_value=fake_response)
    profile = _profile_with_columns("tax_id")
    propose_mapping(profile, hint=None, client=fake_client, schema_config=schema)

    # Inspect the system blocks: the ontology cache block must contain
    # the custom schema_config's text, not the default microbiome shape.
    call_kwargs = fake_client.messages.create.call_args.kwargs
    system_blocks = call_kwargs["system"]
    ontology_block = next(
        b for b in system_blocks
        if isinstance(b, dict) and "micromap_ontology" in (b.get("text") or "")
    )
    assert "custom-llm-test" in ontology_block["text"]
    assert "my_taxon_id" in ontology_block["text"]
    # The default microbiome name MUST NOT leak in.
    assert "micromap-microbiome" not in ontology_block["text"]


# ---------------------------------------------------------------------------
# E5: source.sha256 seal at map time (#78)
# ---------------------------------------------------------------------------


def test_draft_heuristic_mapping_writes_source_sha256_when_provided(tmp_path):
    """When called with source_sha256=<hex>, the mapper writes it into
    mapping['source']['sha256']. This is the E5 contract that submit reads."""
    from micromap_mapforge.inspect.dispatch import inspect
    from micromap_mapforge.mapping.mapper import draft_heuristic_mapping

    csv = tmp_path / "data.csv"
    csv.write_text("gene_symbol\nBRCA1\n", encoding="utf-8")
    profile = inspect(str(csv))
    sha = "a" * 64  # any well-formed sha works for this test

    mapping = draft_heuristic_mapping(profile, source_sha256=sha)
    assert mapping["source"].get("sha256") == sha


def test_draft_heuristic_mapping_omits_sha256_when_none(tmp_path):
    """source.sha256 must be ABSENT (not empty string, not null) when the
    caller doesn't provide one. Keeps the field optional in mapping.yaml so
    legacy fixtures / non-CLI callers don't need to change."""
    from micromap_mapforge.inspect.dispatch import inspect
    from micromap_mapforge.mapping.mapper import draft_heuristic_mapping

    csv = tmp_path / "data.csv"
    csv.write_text("gene_symbol\nBRCA1\n", encoding="utf-8")
    profile = inspect(str(csv))

    mapping = draft_heuristic_mapping(profile)  # no source_sha256
    assert "sha256" not in mapping["source"]


def test_propose_mapping_writes_source_sha256_when_provided(monkeypatch, tmp_path):
    """propose_mapping (LLM path) patches the returned mapping's source.sha256
    AFTER the LLM call but BEFORE validation. The LLM doesn't see the sha;
    we set it server-side."""
    from micromap_mapforge.inspect.dispatch import inspect
    from micromap_mapforge.mapping.mapper import propose_mapping

    csv = tmp_path / "data.csv"
    csv.write_text("gene_symbol\nBRCA1\n", encoding="utf-8")
    profile = inspect(str(csv))
    sha = "b" * 64

    # Mock the anthropic client so we don't make a real LLM call.
    class _FakeBlock:
        def __init__(self, text): self.text = text

    class _FakeResponse:
        def __init__(self, text):
            self.content = [_FakeBlock(text)]

    class _FakeMessages:
        def create(self, **_kwargs):
            # Return a minimal valid mapping with no sha256 -- the mapper
            # must patch it in after we return.
            return _FakeResponse(
                'source:\n'
                f'  name: {csv.stem}\n'
                '  format: csv\n'
                f'  path: {csv}\n'
                'entities:\n'
                '  - label: Gene\n'
                '    match_on: symbol\n'
                '    columns:\n'
                '      symbol: gene_symbol\n'
                'relationships: []\n'
            )

    class _FakeClient:
        def __init__(self):
            self.messages = _FakeMessages()

    mapping = propose_mapping(profile, client=_FakeClient(), source_sha256=sha)
    assert mapping["source"].get("sha256") == sha
