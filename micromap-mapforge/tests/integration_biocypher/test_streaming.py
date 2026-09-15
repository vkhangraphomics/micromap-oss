"""Tests for the streaming BioCypher import path (#374).

Built to fix a real crash: the existing run_adapter -> build_contribution_bundle
-> serialize_bundle chain was killed by a memory-usage guard when building a
bundle from PrimeKG's real ~129k nodes / ~8.1M edges. See
docs/superpowers/specs/2026-09-12-biocypher-streaming-import-design.md.
"""

import io
import json

from micromap_mapforge.integration.biocypher.streaming import _JsonArrayWriter


def test_empty_array():
    buf = io.StringIO()
    writer = _JsonArrayWriter(buf)
    writer.close()
    assert json.loads(buf.getvalue()) == []


def test_single_item():
    buf = io.StringIO()
    writer = _JsonArrayWriter(buf)
    writer.write_item({"a": 1})
    writer.close()
    assert json.loads(buf.getvalue()) == [{"a": 1}]


def test_multiple_items_are_comma_separated():
    buf = io.StringIO()
    writer = _JsonArrayWriter(buf)
    writer.write_item({"a": 1})
    writer.write_item({"b": 2})
    writer.write_item({"c": 3})
    writer.close()
    assert json.loads(buf.getvalue()) == [{"a": 1}, {"b": 2}, {"c": 3}]


def test_output_is_compact_not_pretty_printed():
    buf = io.StringIO()
    writer = _JsonArrayWriter(buf)
    writer.write_item({"a": 1})
    writer.close()
    # No indentation whitespace -- these files are machine-read, never
    # asserted on for formatting by any consumer.
    assert buf.getvalue() == '[{"a": 1}]'


def test_values_needing_json_escaping():
    buf = io.StringIO()
    writer = _JsonArrayWriter(buf)
    writer.write_item({"name": 'off-label "use"', "score": 0.85})
    writer.close()
    assert json.loads(buf.getvalue()) == [{"name": 'off-label "use"', "score": 0.85}]


import pytest

from micromap_mapforge.integration.biocypher.streaming import _stream_nodes


class _TinyNodeAdapter:
    """Two labels, one node each -- enough to prove per-label file routing,
    the label_index, and the entities_block shape, without any real adapter."""

    def get_nodes(self):
        yield ("NCBITaxon:9606", "OrganismTaxon", {"name": "Homo sapiens"},
               {"source": "test"}, "EXTRACTED", None)
        yield ("MONDO:0005148", "Disease", {"name": "Type 2 diabetes"},
               {"source": "test"}, None, None)


def test_stream_nodes_writes_one_cypher_and_params_file_per_label(tmp_path):
    cypher_dir = tmp_path / "cypher"
    cypher_dir.mkdir()
    ir_buf = __import__("io").StringIO()
    ir_writer = _JsonArrayWriter(ir_buf)

    label_index, entities, warnings, count = _stream_nodes(
        _TinyNodeAdapter(), cypher_dir, "org-test", ir_writer
    )
    ir_writer.close()

    assert warnings == []
    assert count == 2
    assert (cypher_dir / "nodes_OrganismTaxon.cypher").exists()
    assert (cypher_dir / "nodes_Disease.cypher").exists()

    taxon_cy = (cypher_dir / "nodes_OrganismTaxon.cypher").read_text(encoding="utf-8")
    assert "MERGE (n:OrganismTaxon {id: row.merge_value, organization_id: $organization_id})" in taxon_cy

    taxon_params = json.loads((cypher_dir / "nodes_OrganismTaxon.params.json").read_text(encoding="utf-8"))
    assert taxon_params == {
        "organization_id": "org-test",
        "batch_id": [{"merge_value": "NCBITaxon:9606", "props": {
            "name": "Homo sapiens", "provenance_source": "test", "confidence": "EXTRACTED",
        }}],
    }


def test_stream_nodes_builds_label_index(tmp_path):
    cypher_dir = tmp_path / "cypher"
    cypher_dir.mkdir()
    ir_writer = _JsonArrayWriter(__import__("io").StringIO())

    label_index, _entities, _warnings, _count = _stream_nodes(
        _TinyNodeAdapter(), cypher_dir, "org-test", ir_writer
    )
    ir_writer.close()

    assert label_index == {
        "NCBITaxon:9606": ("OrganismTaxon", "id"),
        "MONDO:0005148": ("Disease", "id"),
    }


def test_stream_nodes_entities_block_matches_existing_shape(tmp_path):
    cypher_dir = tmp_path / "cypher"
    cypher_dir.mkdir()
    ir_writer = _JsonArrayWriter(__import__("io").StringIO())

    _label_index, entities, _warnings, _count = _stream_nodes(
        _TinyNodeAdapter(), cypher_dir, "org-test", ir_writer
    )
    ir_writer.close()

    by_label = {e["label"]: e for e in entities}
    assert by_label["OrganismTaxon"]["match_on"] == "id"
    assert by_label["OrganismTaxon"]["columns"] == {"id": "id", "name": "name"}
    assert by_label["Disease"]["columns"] == {"id": "id", "name": "name"}


def test_stream_nodes_feeds_ir_json_writer():
    ir_buf = __import__("io").StringIO()
    ir_writer = _JsonArrayWriter(ir_buf)
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as d:
        _stream_nodes(_TinyNodeAdapter(), pathlib.Path(d), "org-test", ir_writer)
    ir_writer.close()

    nodes = json.loads(ir_buf.getvalue())
    assert len(nodes) == 2
    taxon = next(n for n in nodes if n["id"] == "NCBITaxon:9606")
    assert taxon["label"] == "OrganismTaxon"
    assert taxon["properties"] == {"name": "Homo sapiens"}
    assert taxon["confidence"] == "EXTRACTED"
    assert "tier" not in taxon  # None-valued keys stripped, matching bundle_to_schema_dict


def test_stream_nodes_rejects_wrong_arity_tuple(tmp_path):
    class _BadAdapter:
        def get_nodes(self):
            yield ("id-only",)

    with pytest.raises(ValueError, match="wrong arity"):
        _stream_nodes(_BadAdapter(), tmp_path, "org-test", _JsonArrayWriter(__import__("io").StringIO()))


from micromap_mapforge.integration.biocypher.streaming import _stream_edges


def test_stream_edges_labeled_match_when_endpoints_consistent(tmp_path):
    class _Adapter:
        def get_edges(self):
            yield ("MEMBER_OF", "NCBITaxon:9606", "NCBITaxon:9605",
                   {"evidence": "ncbi"}, {"source": "test"}, "EXTRACTED", None)

    label_index = {
        "NCBITaxon:9606": ("OrganismTaxon", "id"),
        "NCBITaxon:9605": ("OrganismTaxon", "id"),
    }
    ir_writer = _JsonArrayWriter(__import__("io").StringIO())

    rels, warnings, count = _stream_edges(_Adapter(), tmp_path, "org-test", label_index, ir_writer)
    ir_writer.close()

    assert warnings == []
    assert count == 1
    cy = (tmp_path / "rels_MEMBER_OF.cypher").read_text(encoding="utf-8")
    assert "MATCH (a:OrganismTaxon {id: row.from})" in cy
    assert "(b:OrganismTaxon {id: row.to})" in cy
    assert "MERGE (a)-[r:MEMBER_OF {organization_id: $organization_id}]->(b)" in cy

    params = json.loads((tmp_path / "rels_MEMBER_OF.params.json").read_text(encoding="utf-8"))
    assert params["batch_id__id"] == [{
        "from": "NCBITaxon:9606", "to": "NCBITaxon:9605",
        "props": {"evidence": "ncbi", "provenance_source": "test", "confidence": "EXTRACTED"},
    }]

    assert rels == [{
        "type": "MEMBER_OF",
        "from": "OrganismTaxon(id=row.from)",
        "to": "OrganismTaxon(id=row.to)",
        "properties": {"evidence": "evidence"},
    }]


def test_stream_edges_unlabeled_match_for_cross_batch_endpoint(tmp_path):
    class _Adapter:
        def get_edges(self):
            yield ("MEMBER_OF", "NCBITaxon:9606", "NCBITaxon:UNKNOWN",
                   {}, {"source": "test"}, None, None)

    label_index = {"NCBITaxon:9606": ("OrganismTaxon", "id")}
    ir_writer = _JsonArrayWriter(__import__("io").StringIO())

    _rels, warnings, _count = _stream_edges(_Adapter(), tmp_path, "org-test", label_index, ir_writer)
    ir_writer.close()

    assert len(warnings) == 1
    assert "to_id=NCBITaxon:UNKNOWN not present" in warnings[0]

    cy = (tmp_path / "rels_MEMBER_OF.cypher").read_text(encoding="utf-8")
    assert "MATCH (a:OrganismTaxon {id: row.from})" in cy
    assert "(b {id: row.to})" in cy  # unlabeled -- endpoint not resolvable


def test_stream_edges_relationship_type_with_special_characters_is_escaped(tmp_path):
    class _Adapter:
        def get_edges(self):
            yield ("OFF-LABEL USE", "Drug:1", "Disease:1",
                   {}, {"source": "primekg"}, "EXTRACTED", None)

    label_index = {"Drug:1": ("Drug", "id"), "Disease:1": ("Disease", "id")}
    ir_writer = _JsonArrayWriter(__import__("io").StringIO())

    _rels, _warnings, _count = _stream_edges(_Adapter(), tmp_path, "org-test", label_index, ir_writer)
    ir_writer.close()

    cy = (tmp_path / "rels_OFF-LABEL_USE.cypher").read_text(encoding="utf-8")
    assert "MERGE (a)-[r:`OFF-LABEL USE` {organization_id: $organization_id}]->(b)" in cy


def test_stream_edges_feeds_ir_json_writer(tmp_path):
    class _Adapter:
        def get_edges(self):
            yield ("MEMBER_OF", "A:1", "A:2", {}, {"source": "t"}, None, None)

    ir_buf = __import__("io").StringIO()
    ir_writer = _JsonArrayWriter(ir_buf)
    _stream_edges(_Adapter(), tmp_path, "org-test", {"A:1": ("X", "id"), "A:2": ("X", "id")}, ir_writer)
    ir_writer.close()

    edges = json.loads(ir_buf.getvalue())
    assert edges == [{"type": "MEMBER_OF", "from_id": "A:1", "to_id": "A:2",
                       "properties": {}, "provenance": {"source": "t"}}]


def test_stream_edges_rejects_wrong_arity_tuple(tmp_path):
    class _BadAdapter:
        def get_edges(self):
            yield ("type-only",)

    with pytest.raises(ValueError, match="wrong arity"):
        _stream_edges(_BadAdapter(), tmp_path, "org-test", {}, _JsonArrayWriter(__import__("io").StringIO()))


def test_stream_edges_raises_clear_error_on_two_pass_edge_type_drift(tmp_path):
    """_stream_edges makes two passes over adapter.get_edges() and assumes
    the set of edge types is identical on both. A real, if unusual, adapter
    bug (e.g. reading from a mutating iterator/generator with side effects)
    could violate that and yield a genuinely different edge type on the
    second pass. Before Fix 6 (#374 final review) this hit a bare
    uninterpretable KeyError deep in a long-running real-data import; it
    should instead raise a ValueError naming the adapter and the offending
    type."""

    class _DriftingAdapter:
        name = "drifting-adapter"

        def __init__(self):
            self.calls = 0

        def get_edges(self):
            self.calls += 1
            if self.calls == 1:
                yield ("MEMBER_OF", "A:1", "A:2", {}, {"source": "t"}, None, None)
            else:
                # Second pass yields a type never seen on the first pass --
                # files[e_type] was never opened for it.
                yield ("DIFFERENT_TYPE", "A:1", "A:2", {}, {"source": "t"}, None, None)

    label_index = {"A:1": ("X", "id"), "A:2": ("X", "id")}
    ir_writer = _JsonArrayWriter(__import__("io").StringIO())

    with pytest.raises(ValueError) as exc_info:
        _stream_edges(_DriftingAdapter(), tmp_path, "org-test", label_index, ir_writer)

    msg = str(exc_info.value)
    assert "drifting-adapter" in msg
    assert "DIFFERENT_TYPE" in msg
    assert "second get_edges() pass" in msg


import yaml as _yaml

from micromap_mapforge.integration.biocypher.ir import SourceRef
from micromap_mapforge.integration.biocypher.streaming import stream_adapter_to_bundle


class _FakeGtdbLikeAdapter:
    name = "fake-gtdb"
    schema_config = {"name": "biolink-mini", "prefixes": {"NCBITaxon": "http://x/"}}

    def get_nodes(self):
        yield ("NCBITaxon:9606", "OrganismTaxon", {"name": "Homo sapiens"},
               {"source": "gtdb", "method": "curated"}, "EXTRACTED", None)
        yield ("NCBITaxon:9605", "OrganismTaxon", {"name": "Homo"},
               {"source": "gtdb", "method": "curated"}, "EXTRACTED", None)

    def get_edges(self):
        yield ("MEMBER_OF", "NCBITaxon:9606", "NCBITaxon:9605",
               {"evidence": "ncbi"}, {"source": "gtdb", "method": "curated"},
               "EXTRACTED", None)


def _fake_source(tmp_path) -> SourceRef:
    src = tmp_path / "fake.tsv"
    src.write_text("ignored", encoding="utf-8")
    return SourceRef(kind="file", path=str(src), sha256="0" * 64)


def test_stream_adapter_to_bundle_writes_full_bundle_shape(tmp_path):
    out = tmp_path / "out"
    stats = stream_adapter_to_bundle(
        _FakeGtdbLikeAdapter(), out,
        organization_id="org-test", source=_fake_source(tmp_path),
    )

    assert stats.nodes_written == 2
    assert stats.edges_written == 1
    assert stats.labels == ["OrganismTaxon"]
    assert stats.edge_types == ["MEMBER_OF"]

    assert (out / "mapping.yaml").exists()
    assert (out / "routing.yaml").exists()
    assert (out / "manifest.json").exists()
    assert (out / "bundle.ir.json").exists()
    assert (out / "cypher" / "nodes_OrganismTaxon.cypher").exists()
    assert (out / "cypher" / "rels_MEMBER_OF.cypher").exists()

    mapping = _yaml.safe_load((out / "mapping.yaml").read_text(encoding="utf-8"))
    assert mapping["source"]["format"] == "schema_adapter"
    assert mapping["entities"][0]["label"] == "OrganismTaxon"

    ir = json.loads((out / "bundle.ir.json").read_text(encoding="utf-8"))
    assert ir["organization_id"] == "org-test"
    assert len(ir["nodes"]) == 2
    assert len(ir["edges"]) == 1

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert "cypher/nodes_OrganismTaxon.cypher" in manifest["files"]
    assert manifest["approved"] is False
    # bundle.ir.json must stay an untracked sidecar (moved into place only
    # AFTER _write_manifest runs), matching serialize.py's sync-path comment
    # and the design spec's Pass-2 ordering -- see Finding 1 (#374 review).
    assert "bundle.ir.json" not in manifest["files"]


def test_stream_adapter_to_bundle_leaves_no_stray_temp_file_on_failure(tmp_path, monkeypatch):
    """A mid-stream failure (malformed adapter tuple reaching _stream_edges)
    must not leave a stray *.bundle.ir.json.tmp file behind in the temp
    directory -- Finding 2 (#374 review): ir_fp must be exception-safe."""
    import tempfile as _tempfile

    class _FailingEdgeAdapter:
        name = "failing-adapter"
        schema_config = {"name": "biolink-mini"}

        def get_nodes(self):
            yield ("A:1", "OrganismTaxon", {"name": "a"}, {"source": "t"}, None, None)

        def get_edges(self):
            yield ("type-only",)  # wrong arity -- raises ValueError in _stream_edges

    tmp_dir = tmp_path / "isolated_tmp"
    tmp_dir.mkdir()
    monkeypatch.setattr(_tempfile, "gettempdir", lambda: str(tmp_dir))

    out = tmp_path / "out"
    with pytest.raises(ValueError, match="wrong arity"):
        stream_adapter_to_bundle(
            _FailingEdgeAdapter(), out,
            organization_id="org-test", source=_fake_source(tmp_path),
        )

    leftover = list(tmp_dir.glob("*.bundle.ir.json.tmp"))
    assert leftover == [], f"stray temp file(s) left behind: {leftover}"


class _ZeroNodeAdapter:
    """An adapter that produces no nodes at all -- the case that used to
    succeed silently with a broken `entities: []` bundle (#374 final review
    Fix 2)."""

    name = "zero-node-adapter"
    schema_config = {"name": "biolink-mini"}

    def get_nodes(self):
        return iter(())

    def get_edges(self):
        return iter(())


def test_stream_adapter_to_bundle_raises_on_zero_nodes(tmp_path):
    """Zero-node parity with serialize.py:89-94 (#374 final review Fix 2):
    a bundle with no nodes and no pre-existing mapping.yaml must fail loudly
    with the identical ValueError message, not silently write a broken
    `entities: []` bundle and let the CLI exit 0."""
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="Cannot serialize a ContributionBundle with no nodes"):
        stream_adapter_to_bundle(
            _ZeroNodeAdapter(), out,
            organization_id="org-test", source=_fake_source(tmp_path),
        )

    # No half-written bundle artifacts left behind.
    assert not (out / "mapping.yaml").exists()
    assert not (out / "manifest.json").exists()
    assert not (out / "bundle.ir.json").exists()


def test_stream_adapter_to_bundle_zero_nodes_leaves_no_stray_temp_file(tmp_path, monkeypatch):
    """Same stray-temp-file guarantee as
    test_stream_adapter_to_bundle_leaves_no_stray_temp_file_on_failure above,
    for the zero-node raise path specifically."""
    import tempfile as _tempfile

    tmp_dir = tmp_path / "isolated_tmp"
    tmp_dir.mkdir()
    monkeypatch.setattr(_tempfile, "gettempdir", lambda: str(tmp_dir))

    out = tmp_path / "out"
    with pytest.raises(ValueError, match="Cannot serialize a ContributionBundle with no nodes"):
        stream_adapter_to_bundle(
            _ZeroNodeAdapter(), out,
            organization_id="org-test", source=_fake_source(tmp_path),
        )

    leftover = list(tmp_dir.glob("*.bundle.ir.json.tmp"))
    assert leftover == [], f"stray temp file(s) left behind: {leftover}"


from micromap_mapforge.integration.biocypher.runner import build_contribution_bundle, run_adapter
from micromap_mapforge.integration.biocypher.serialize import serialize_bundle


def _run_existing_chain(adapter, out_dir, organization_id, source):
    offline = run_adapter(adapter)
    bundle = build_contribution_bundle(offline, organization_id=organization_id, source=source)
    serialize_bundle(bundle, out_dir)


def test_streaming_path_matches_existing_chain_for_fake_gtdb_adapter(tmp_path):
    """The actual proof this rewrite is safe: run both paths over the same
    adapter, diff every file. Uses the project's existing FakeGtdbAdapter
    fixture (imported from test_runner.py, the same one test_cli_import_kg.py
    already exercises against the real CLI) rather than a fresh fixture, so
    this test is grounded in an adapter shape the rest of the suite already
    trusts."""
    from tests.integration_biocypher.test_runner import FakeGtdbAdapter

    source = _fake_source(tmp_path)
    old_out = tmp_path / "old"
    new_out = tmp_path / "new"

    _run_existing_chain(FakeGtdbAdapter(), old_out, "org-test", source)
    stream_adapter_to_bundle(FakeGtdbAdapter(), new_out, organization_id="org-test", source=source)

    # mapping.yaml: parsed-equal (key order isn't semantically meaningful).
    old_mapping = _yaml.safe_load((old_out / "mapping.yaml").read_text(encoding="utf-8"))
    new_mapping = _yaml.safe_load((new_out / "mapping.yaml").read_text(encoding="utf-8"))
    assert old_mapping == new_mapping

    # .cypher files: byte-for-byte (these ARE asserted on for exact text
    # elsewhere in the suite, e.g. test_serialize_golden.py).
    for cy_file in sorted((old_out / "cypher").glob("*.cypher")):
        rel = cy_file.name
        assert (new_out / "cypher" / rel).read_text(encoding="utf-8") == cy_file.read_text(encoding="utf-8")

    # .params.json and bundle.ir.json: parsed-equal (compact vs indented
    # formatting differs by design; no consumer cares about whitespace).
    for params_file in sorted((old_out / "cypher").glob("*.params.json")):
        rel = params_file.name
        old_data = json.loads(params_file.read_text(encoding="utf-8"))
        new_data = json.loads((new_out / "cypher" / rel).read_text(encoding="utf-8"))
        assert old_data == new_data

    old_ir = json.loads((old_out / "bundle.ir.json").read_text(encoding="utf-8"))
    new_ir = json.loads((new_out / "bundle.ir.json").read_text(encoding="utf-8"))
    assert old_ir == new_ir

    # routing.yaml: parsed-equal, except `provenance.submitted_at`. Both paths
    # call the exact same plan_route() (routing.py:44 stamps
    # datetime.now(timezone.utc).isoformat() at call time) via the shared
    # _write_routing_yaml() -- so two sequential invocations, old chain then
    # new streaming chain, always produce two different timestamps regardless
    # of which code path calls it. That's not a streaming-vs-existing-chain
    # discrepancy, so pop it from both sides before comparing; everything else
    # in routing.yaml, including `provenance.mapping_version` (a sha256 of
    # mapping.yaml's content -- already independently verified equal above),
    # is still compared for real equality.
    old_routing = _yaml.safe_load((old_out / "routing.yaml").read_text(encoding="utf-8"))
    new_routing = _yaml.safe_load((new_out / "routing.yaml").read_text(encoding="utf-8"))
    old_routing["provenance"].pop("submitted_at", None)
    new_routing["provenance"].pop("submitted_at", None)
    assert old_routing == new_routing


class _MultiEdgeTypeAdapter:
    """2 node labels, 3 edge types -- one of them PrimeKG's real filename-unsafe
    "OFF-LABEL USE" -- richer than FakeGtdbAdapter's single-label/single-edge-type
    shape. Exercises two gaps FakeGtdbAdapter can't reach:

      - WARNINGS.md parity (Fix 1 / #374 final review): a filename-unsafe
        relationship type must produce a WARNINGS.md entry on the streaming
        path, matching the existing sync path.
      - relationships-list ordering (Fix 4 / #374 final review): with 3
        edge types, alphabetical ("CONTRAINDICATED_FOR", "OFF-LABEL USE",
        "TREATS") and first-seen ("TREATS", "OFF-LABEL USE",
        "CONTRAINDICATED_FOR") orders diverge -- a single-edge-type fixture
        can never tell these apart.
    """

    name = "multi-edge-fixture"
    schema_config = {"name": "biolink-mini"}

    def get_nodes(self):
        yield ("Drug:1", "Drug", {"name": "DrugA"}, {"source": "test"}, "EXTRACTED", None)
        yield ("Drug:2", "Drug", {"name": "DrugB"}, {"source": "test"}, "EXTRACTED", None)
        yield ("Drug:3", "Drug", {"name": "DrugC"}, {"source": "test"}, "EXTRACTED", None)
        yield ("Disease:1", "Disease", {"name": "DiseaseA"}, {"source": "test"}, None, None)
        yield ("Disease:2", "Disease", {"name": "DiseaseB"}, {"source": "test"}, None, None)

    def get_edges(self):
        yield ("TREATS", "Drug:1", "Disease:1",
               {"evidence": "trial"}, {"source": "test"}, "EXTRACTED", None)
        yield ("TREATS", "Drug:2", "Disease:2",
               {"evidence": "trial"}, {"source": "test"}, "EXTRACTED", None)
        yield ("OFF-LABEL USE", "Drug:1", "Disease:2",
               {"evidence": "case-report"}, {"source": "primekg"}, "EXTRACTED", None)
        yield ("CONTRAINDICATED_FOR", "Drug:3", "Disease:1",
               {"evidence": "label"}, {"source": "test"}, "EXTRACTED", None)


def test_streaming_path_matches_existing_chain_for_multi_edge_type_adapter(tmp_path):
    """Second golden-equivalence test: 2 labels x 3 edge types (one
    filename-unsafe: PrimeKG's real "OFF-LABEL USE"), proving the streaming
    path matches the existing chain byte-for-byte (or parsed-equal, same
    convention as the FakeGtdbAdapter golden test) on every file the bundle
    produces -- INCLUDING WARNINGS.md, which the single-edge-type golden
    test above never exercises. This test must fail against the pre-fix
    streaming.py (WARNINGS.md silently dropped; relationships list sorted
    alphabetically instead of first-seen) -- see #374 final review Fixes 1
    and 4."""
    source = _fake_source(tmp_path)
    old_out = tmp_path / "old_multi"
    new_out = tmp_path / "new_multi"

    _run_existing_chain(_MultiEdgeTypeAdapter(), old_out, "org-test", source)
    stream_adapter_to_bundle(_MultiEdgeTypeAdapter(), new_out, organization_id="org-test", source=source)

    # mapping.yaml: parsed-equal, INCLUDING list order -- this is what catches
    # Fix 4 (relationships list must be first-seen order, not sorted()).
    old_mapping = _yaml.safe_load((old_out / "mapping.yaml").read_text(encoding="utf-8"))
    new_mapping = _yaml.safe_load((new_out / "mapping.yaml").read_text(encoding="utf-8"))
    assert old_mapping == new_mapping

    # .cypher files: byte-for-byte.
    for cy_file in sorted((old_out / "cypher").glob("*.cypher")):
        rel = cy_file.name
        assert (new_out / "cypher" / rel).exists(), f"missing {rel} in streaming output"
        assert (new_out / "cypher" / rel).read_text(encoding="utf-8") == cy_file.read_text(encoding="utf-8")

    # .params.json: parsed-equal.
    for params_file in sorted((old_out / "cypher").glob("*.params.json")):
        rel = params_file.name
        old_data = json.loads(params_file.read_text(encoding="utf-8"))
        new_data = json.loads((new_out / "cypher" / rel).read_text(encoding="utf-8"))
        assert old_data == new_data

    # bundle.ir.json: parsed-equal.
    old_ir = json.loads((old_out / "bundle.ir.json").read_text(encoding="utf-8"))
    new_ir = json.loads((new_out / "bundle.ir.json").read_text(encoding="utf-8"))
    assert old_ir == new_ir

    # routing.yaml: parsed-equal except provenance.submitted_at (see the
    # FakeGtdbAdapter golden test above for why that field always differs).
    old_routing = _yaml.safe_load((old_out / "routing.yaml").read_text(encoding="utf-8"))
    new_routing = _yaml.safe_load((new_out / "routing.yaml").read_text(encoding="utf-8"))
    old_routing["provenance"].pop("submitted_at", None)
    new_routing["provenance"].pop("submitted_at", None)
    assert old_routing == new_routing

    # WARNINGS.md: the gap the final review found. With "OFF-LABEL USE"
    # present, the sync path writes a WARNINGS.md entry for the
    # filename-unsafe relationship type; the streaming path must too
    # (Fix 1), byte-for-byte identical.
    old_warnings_path = old_out / "WARNINGS.md"
    new_warnings_path = new_out / "WARNINGS.md"
    assert old_warnings_path.exists(), "sanity: sync path should warn about OFF-LABEL USE"
    assert new_warnings_path.exists(), "streaming path silently dropped WARNINGS.md (Fix 1 regression)"
    assert new_warnings_path.read_text(encoding="utf-8") == old_warnings_path.read_text(encoding="utf-8")
