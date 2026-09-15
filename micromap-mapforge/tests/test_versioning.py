"""KG version diff — C8 (#76)."""
from micromap_mapforge.benchmarks.synth import (
    fall_through_report,
    synth_mapping,
    synth_rows,
    synth_source_ref,
)
from micromap_mapforge.integration.biocypher.serialize import serialize_bundle
from micromap_mapforge.integration.tabular import tabular_to_ir
from micromap_mapforge.versioning import (
    SNAPSHOT_FILENAME,
    diff_bundle_dirs,
    diff_bundles,
    load_ir_snapshot,
)


def _bundle(n_rows):
    return tabular_to_ir(
        mapping=synth_mapping(), rows=synth_rows(n_rows), report=fall_through_report(),
        organization_id="org", source_ref=synth_source_ref(),
    )


# --- diff engine (plain schema-dicts) ---

def test_diff_added_removed_changed():
    old = {
        "nodes": [
            {"label": "Taxon", "id": "1", "properties": {"name": "a"}},
            {"label": "Taxon", "id": "2", "properties": {"name": "b"}},
        ],
        "edges": [{"type": "R", "from_id": "1", "to_id": "2", "properties": {}}],
    }
    new = {
        "nodes": [
            {"label": "Taxon", "id": "2", "properties": {"name": "B"}},   # changed
            {"label": "Taxon", "id": "3", "properties": {"name": "c"}},   # added
        ],
        "edges": [{"type": "R", "from_id": "2", "to_id": "3", "properties": {}}],  # added
    }
    d = diff_bundles(old, new)
    assert {n["id"] for n in d.nodes_added} == {"3"}
    assert {n["id"] for n in d.nodes_removed} == {"1"}
    assert [c["id"] for c in d.nodes_changed] == ["2"]
    assert d.nodes_changed[0]["before"] == {"name": "b"}
    assert d.nodes_changed[0]["after"] == {"name": "B"}
    assert len(d.edges_added) == 1 and len(d.edges_removed) == 1
    assert d.summary() == {
        "nodes_added": 1, "nodes_removed": 1, "nodes_changed": 1,
        "edges_added": 1, "edges_removed": 1, "edges_changed": 0,
    }
    assert not d.is_empty()


def test_identical_bundles_have_empty_diff():
    b = {"nodes": [{"label": "T", "id": "1", "properties": {}}], "edges": []}
    assert diff_bundles(b, b).is_empty()


# --- snapshot persistence + dir diff ---

def test_serialize_writes_ir_snapshot(tmp_path):
    serialize_bundle(_bundle(3), tmp_path / "v1")
    snap = tmp_path / "v1" / SNAPSHOT_FILENAME
    assert snap.is_file()
    payload = load_ir_snapshot(tmp_path / "v1")           # load via dir
    assert payload == load_ir_snapshot(snap)              # load via file
    assert len(payload["nodes"]) == 6 and len(payload["edges"]) == 3  # 3 taxa + 3 diseases


def test_missing_snapshot_raises(tmp_path):
    import pytest
    (tmp_path / "empty").mkdir()
    with pytest.raises(FileNotFoundError):
        load_ir_snapshot(tmp_path / "empty")


def test_diff_two_serialized_versions(tmp_path):
    serialize_bundle(_bundle(5), tmp_path / "v89")
    serialize_bundle(_bundle(7), tmp_path / "v90")   # 2 more rows
    d = diff_bundle_dirs(tmp_path / "v89", tmp_path / "v90")
    s = d.summary()
    assert s["nodes_added"] == 4   # 2 new taxa + 2 new diseases
    assert s["edges_added"] == 2   # 2 new ASSOCIATED_WITH
    assert s["nodes_removed"] == 0 and s["edges_removed"] == 0


# --- CLI ---

def _run(args):
    from click.testing import CliRunner

    from micromap_mapforge.cli import main
    return CliRunner().invoke(main, args)


def test_cli_diff_summary_and_json(tmp_path):
    serialize_bundle(_bundle(5), tmp_path / "v89")
    serialize_bundle(_bundle(7), tmp_path / "v90")

    res = _run(["diff", str(tmp_path / "v89"), str(tmp_path / "v90")])
    assert res.exit_code == 0
    assert "nodes: +4 -0 ~0" in res.output and "edges: +2 -0 ~0" in res.output

    res_json = _run(["diff", str(tmp_path / "v89"), str(tmp_path / "v90"), "--json"])
    import json
    payload = json.loads(res_json.output)
    assert payload["summary"]["nodes_added"] == 4


def test_cli_diff_no_changes(tmp_path):
    serialize_bundle(_bundle(4), tmp_path / "a")
    serialize_bundle(_bundle(4), tmp_path / "b")
    res = _run(["diff", str(tmp_path / "a"), str(tmp_path / "b")])
    assert "no changes" in res.output


def test_cli_diff_missing_snapshot_exit_2(tmp_path):
    (tmp_path / "x").mkdir()
    serialize_bundle(_bundle(2), tmp_path / "y")
    res = _run(["diff", str(tmp_path / "x"), str(tmp_path / "y")])
    assert res.exit_code == 2 and "no IR snapshot" in (res.output + str(res.exception or ""))
