"""Cross-source SAME_AS hints — D4 (#77)."""
import json

from micromap_mapforge.unification import (
    _identity_tokens,
    propose_same_as,
    propose_same_as_dirs,
)


def _node(label, id_, **props):
    return {"label": label, "id": id_, "properties": props}


def test_bare_id_property_unifies_with_curie_keyed_node():
    # Source A: a Compound keyed on DrugBank, carrying a bare chembl_id.
    a = {"nodes": [_node("Compound", "DrugBank:DB00945", chembl_id="CHEMBL25")]}
    # Source B: the same molecule keyed on the ChEMBL CURIE.
    b = {"nodes": [_node("Compound", "CHEMBL:CHEMBL25")]}
    cands = propose_same_as(a, b)
    assert len(cands) == 1
    c = cands[0]
    assert c["label"] == "Compound"
    assert c["a_id"] == "DrugBank:DB00945" and c["b_id"] == "CHEMBL:CHEMBL25"
    assert "chembl:CHEMBL25" in c["shared"]


def test_curie_prefix_casing_normalized():
    a = {"nodes": [_node("Gene", "hgnc:1100")]}
    b = {"nodes": [_node("Gene", "HGNC:1100")]}
    cands = propose_same_as(a, b)
    assert len(cands) == 1 and cands[0]["shared"] == ["hgnc:1100"]


def test_no_overlap_no_candidates():
    a = {"nodes": [_node("Compound", "CHEMBL:CHEMBL1")]}
    b = {"nodes": [_node("Compound", "CHEMBL:CHEMBL999")]}
    assert propose_same_as(a, b) == []


def test_identical_primary_id_is_not_a_candidate():
    # Same canonical node in both runs -> within-run dedup, not a SAME_AS hint.
    a = {"nodes": [_node("Compound", "CHEMBL:CHEMBL25")]}
    b = {"nodes": [_node("Compound", "CHEMBL:CHEMBL25")]}
    assert propose_same_as(a, b) == []


def test_different_labels_do_not_unify():
    a = {"nodes": [_node("Gene", "x:1")]}
    b = {"nodes": [_node("Disease", "x:1")]}
    assert propose_same_as(a, b) == []


def test_identity_tokens_synthesizes_curie_from_key():
    toks = _identity_tokens(_node("Compound", "DrugBank:DB1", chembl_id="CHEMBL25"))
    assert "chembl:CHEMBL25" in toks and "CHEMBL25" in toks


def test_propose_same_as_dirs_and_cli(tmp_path):
    from click.testing import CliRunner

    from micromap_mapforge.cli import main

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "bundle.ir.json").write_text(json.dumps(
        {"nodes": [_node("Compound", "DrugBank:DB00945", chembl_id="CHEMBL25")], "edges": []}),
        encoding="utf-8")
    (tmp_path / "b" / "bundle.ir.json").write_text(json.dumps(
        {"nodes": [_node("Compound", "CHEMBL:CHEMBL25")], "edges": []}),
        encoding="utf-8")

    cands = propose_same_as_dirs(tmp_path / "a", tmp_path / "b")
    assert len(cands) == 1

    res = CliRunner().invoke(main, ["unify", str(tmp_path / "a"), str(tmp_path / "b")])
    assert res.exit_code == 0
    assert "SAME_AS candidate" in res.output and "DrugBank:DB00945" in res.output

    res_empty = CliRunner().invoke(main, ["unify", str(tmp_path / "a"), str(tmp_path / "a")])
    assert "no cross-source SAME_AS candidates" in res_empty.output
