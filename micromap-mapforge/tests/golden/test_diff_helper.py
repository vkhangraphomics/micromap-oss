"""Unit tests for tests/golden/_diff.py (3b-4 / #128).

These tests document the expected output format so future fixture
re-greenings produce reviewable diffs, not walls of YAML/JSON.
"""

from __future__ import annotations

from pathlib import Path


from tests.golden._diff import (
    diff_file_pair,
    diff_json,
    diff_structured,
    diff_text,
    diff_yaml,
)


def test_diff_text_returns_empty_string_on_equal():
    assert diff_text("a\nb\n", "a\nb\n") == ""


def test_diff_text_shows_unified_diff_with_context():
    g = "line1\nline2-changed\nline3\n"
    e = "line1\nline2\nline3\n"
    out = diff_text(g, e)
    assert "-line2" in out
    assert "+line2-changed" in out
    # context lines present
    assert "line1" in out and "line3" in out


def test_diff_structured_empty_on_equal_dicts():
    assert diff_structured({"a": 1, "b": 2}, {"a": 1, "b": 2}) == []


def test_diff_structured_added_key():
    out = diff_structured({"a": 1, "b": 2}, {"a": 1})
    assert out == ["+ b = 2"]


def test_diff_structured_removed_key():
    out = diff_structured({"a": 1}, {"a": 1, "b": 2})
    assert out == ["- b (was 2)"]


def test_diff_structured_changed_value():
    out = diff_structured({"a": 1}, {"a": 2})
    assert out == ["~ a: 2 → 1"]


def test_diff_structured_nested_path():
    out = diff_structured({"a": {"b": {"c": 1}}}, {"a": {"b": {"c": 2}}})
    assert out == ["~ a.b.c: 2 → 1"]


def test_diff_structured_list_length_changed():
    out = diff_structured([1, 2, 3], [1, 2])
    # length annotation + the new element
    assert any("length 2 → 3" in d for d in out)
    assert any("[2] = 3" in d for d in out)


def test_diff_structured_list_element_changed():
    out = diff_structured([{"x": 1}, {"y": 2}], [{"x": 1}, {"y": 3}])
    assert out == ["~ [1].y: 3 → 2"]


def test_diff_yaml_structural_diff_on_keys():
    g = "a: 1\nb: 3\n"
    e = "a: 1\nb: 2\n"
    out = diff_yaml(g, e)
    assert "~ b: 2 → 3" in out


def test_diff_yaml_falls_back_to_text_on_unparseable():
    out = diff_yaml("not yaml: [", "not yaml: ]")
    # falls back to unified text diff
    assert "---" in out or "@@" in out


def test_diff_json_structural_diff_on_keys():
    g = '{"a": 1, "b": 3}'
    e = '{"a": 1, "b": 2}'
    out = diff_json(g, e)
    assert "~ b: 2 → 3" in out


def test_diff_file_pair_dispatches_by_suffix(tmp_path: Path):
    (tmp_path / "g.json").write_text('{"a": 1, "b": 3}', encoding="utf-8")
    (tmp_path / "e.json").write_text('{"a": 1, "b": 2}', encoding="utf-8")
    out = diff_file_pair(tmp_path / "g.json", tmp_path / "e.json")
    assert "~ b: 2 → 3" in out


def test_diff_file_pair_empty_on_identical_files(tmp_path: Path):
    (tmp_path / "g.cypher").write_text("MATCH (n) RETURN n;\n", encoding="utf-8")
    (tmp_path / "e.cypher").write_text("MATCH (n) RETURN n;\n", encoding="utf-8")
    assert diff_file_pair(tmp_path / "g.cypher", tmp_path / "e.cypher") == ""


def test_diff_file_pair_cypher_uses_text_diff(tmp_path: Path):
    (tmp_path / "g.cypher").write_text(
        "UNWIND $batch_id AS row\nMERGE (n:Taxon {id: row.merge_value})\n",
        encoding="utf-8",
    )
    (tmp_path / "e.cypher").write_text(
        "UNWIND $batch_id AS row\nMATCH (n:Taxon {id: row.merge_value})\n",
        encoding="utf-8",
    )
    out = diff_file_pair(tmp_path / "g.cypher", tmp_path / "e.cypher")
    # MERGE → MATCH change visible in unified diff
    assert "MERGE" in out and "MATCH" in out
