"""#152: `mapforge templates diff` — semantic diff between two template versions.

Templates are single-file (version bumped in place, see test_template_versioning.py),
so a prior version's body has to be resolved from git history rather than a
`<name>/<version>.yaml` archive. `resolve_version_payload` does that; `semantic_diff`
is the pure comparison the CLI prints.
"""
import subprocess
from pathlib import Path

import pytest
import yaml

from micromap_mapforge.mapping import template_diff as td


# -- semantic_diff: pure, no git involved -----------------------------------

def test_diff_detects_added_class():
    old = {"classes": {"Taxon": {}}}
    new = {"classes": {"Taxon": {}, "Disease": {}}}
    result = td.semantic_diff(old, new)
    assert result["classes_added"] == ["Disease"]


def test_diff_detects_removed_class():
    old = {"classes": {"Taxon": {}, "Disease": {}}}
    new = {"classes": {"Taxon": {}}}
    result = td.semantic_diff(old, new)
    assert result["classes_removed"] == ["Disease"]


def test_diff_detects_identifier_added():
    old = {"classes": {"Compound": {"x_mapforge": {"identifiers": ["compound_id"]}}}}
    new = {"classes": {"Compound": {"x_mapforge": {"identifiers": ["compound_id", "hmdb_id"]}}}}
    result = td.semantic_diff(old, new)
    assert result["classes_changed"]["Compound"]["identifiers_added"] == ["hmdb_id"]


def test_diff_detects_identifier_removed():
    old = {"classes": {"Compound": {"x_mapforge": {"identifiers": ["compound_id", "hmdb_id"]}}}}
    new = {"classes": {"Compound": {"x_mapforge": {"identifiers": ["compound_id"]}}}}
    result = td.semantic_diff(old, new)
    assert result["classes_changed"]["Compound"]["identifiers_removed"] == ["hmdb_id"]


def test_diff_detects_notes_changed():
    old = {"classes": {"Taxon": {"x_mapforge": {"notes": "old note"}}}}
    new = {"classes": {"Taxon": {"x_mapforge": {"notes": "new note"}}}}
    result = td.semantic_diff(old, new)
    assert result["classes_changed"]["Taxon"]["notes_changed"] is True


def test_diff_ignores_unchanged_classes():
    old = {"classes": {"Taxon": {"slots": ["id", "name"]}}}
    new = {"classes": {"Taxon": {"slots": ["id", "name"]}}}
    result = td.semantic_diff(old, new)
    assert result["classes_added"] == []
    assert result["classes_removed"] == []
    assert result["classes_changed"] == {}


def test_diff_detects_other_field_change():
    old = {"classes": {"Taxon": {"slots": ["id", "name"]}}}
    new = {"classes": {"Taxon": {"slots": ["id", "name", "rank"]}}}
    result = td.semantic_diff(old, new)
    assert result["classes_changed"]["Taxon"]["other_fields_changed"] == ["slots"]


# -- resolve_version_payload: real temp git repo, no mocks -------------------

def _init_repo_with_two_versions(tmp_path: Path) -> Path:
    """A minimal real git repo with one template file bumped 1.0.0 -> 1.1.0
    across two commits, mirroring versions.lock's actual discipline."""
    repo = tmp_path / "repo"
    templates_dir = repo / "micromap_mapforge" / "mapping" / "templates"
    templates_dir.mkdir(parents=True)

    def run(*args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)

    run("init", "-q")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "test")

    tmpl = templates_dir / "widget.yaml"
    tmpl.write_text('version: "1.0.0"\nclasses:\n  Thing: {}\n', encoding="utf-8")
    run("add", "-A")
    run("commit", "-q", "-m", "v1.0.0")

    tmpl.write_text('version: "1.1.0"\nclasses:\n  Thing: {}\n  Gadget: {}\n', encoding="utf-8")
    run("add", "-A")
    run("commit", "-q", "-m", "v1.1.0")

    return templates_dir


def _init_repo_with_unicode_notes(tmp_path: Path) -> tuple[Path, str]:
    """Two commits whose body has a non-ASCII character (an em-dash) —
    regression fixture for a `_git()` decoding bug: subprocess text-mode
    decoding without an explicit encoding uses the platform locale codec,
    which on Windows mangled an em-dash that a direct UTF-8 file read got
    right, producing a false diff between byte-identical content."""
    repo = tmp_path / "repo"
    templates_dir = repo / "micromap_mapforge" / "mapping" / "templates"
    templates_dir.mkdir(parents=True)

    def run(*args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)

    run("init", "-q")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "test")

    tmpl = templates_dir / "widget.yaml"
    content = 'version: "1.0.0"\nclasses:\n  Thing:\n    notes: "legacy path — disjoint-node"\n'
    tmpl.write_text(content, encoding="utf-8")
    run("add", "-A")
    run("commit", "-q", "-m", "v1.0.0")

    # A second commit so resolving "1.0.0" goes through git show (history),
    # not the live-file fast path — that's the code path with the bug.
    tmpl.write_text(content.replace("1.0.0", "1.1.0"), encoding="utf-8")
    run("add", "-A")
    run("commit", "-q", "-m", "v1.1.0")

    return templates_dir, content


def test_resolve_historical_version_decodes_non_ascii_correctly(tmp_path, monkeypatch):
    """A historical version (resolved via `git show`, through subprocess text
    decoding) must decode a non-ASCII character identically to a direct UTF-8
    file read — not mangle it into a platform-codec-dependent replacement
    character."""
    templates_dir, content = _init_repo_with_unicode_notes(tmp_path)
    monkeypatch.setattr(td, "TEMPLATES_DIR", templates_dir)

    payload = td.resolve_version_payload("widget", "1.0.0")

    expected = yaml.safe_load(content)
    assert payload["classes"]["Thing"]["notes"] == expected["classes"]["Thing"]["notes"]
    assert "—" in payload["classes"]["Thing"]["notes"]


def test_resolve_current_version_reads_live_file(tmp_path, monkeypatch):
    templates_dir = _init_repo_with_two_versions(tmp_path)
    monkeypatch.setattr(td, "TEMPLATES_DIR", templates_dir)

    payload = td.resolve_version_payload("widget", "1.1.0")

    assert payload["version"] == "1.1.0"
    assert "Gadget" in payload["classes"]


def test_resolve_historical_version_from_git_history(tmp_path, monkeypatch):
    templates_dir = _init_repo_with_two_versions(tmp_path)
    monkeypatch.setattr(td, "TEMPLATES_DIR", templates_dir)

    payload = td.resolve_version_payload("widget", "1.0.0")

    assert payload["version"] == "1.0.0"
    assert "Gadget" not in payload["classes"]


def test_resolve_unknown_version_raises(tmp_path, monkeypatch):
    templates_dir = _init_repo_with_two_versions(tmp_path)
    monkeypatch.setattr(td, "TEMPLATES_DIR", templates_dir)

    with pytest.raises(td.VersionNotFoundError, match="2.0.0"):
        td.resolve_version_payload("widget", "2.0.0")


def test_resolve_unknown_template_raises(tmp_path, monkeypatch):
    templates_dir = _init_repo_with_two_versions(tmp_path)
    monkeypatch.setattr(td, "TEMPLATES_DIR", templates_dir)

    with pytest.raises(td.VersionNotFoundError, match="nonexistent"):
        td.resolve_version_payload("nonexistent", "1.0.0")


def test_resolve_from_shallow_clone_gives_actionable_error(tmp_path, monkeypatch):
    """CI (and any `git clone --depth 1`) truncates history — the 1.0.0 commit
    is simply gone, not merely unmatched. The tool should say so, not report
    the generic (and here misleading) 'no such version'."""
    origin_templates_dir = _init_repo_with_two_versions(tmp_path)
    origin = origin_templates_dir.parents[2]  # repo/ (see the helper above)

    shallow = tmp_path / "shallow-clone"
    subprocess.run(
        ["git", "clone", "--depth", "1", origin.as_uri(), str(shallow)],
        check=True, capture_output=True, text=True,
    )
    shallow_templates_dir = shallow / "micromap_mapforge" / "mapping" / "templates"
    monkeypatch.setattr(td, "TEMPLATES_DIR", shallow_templates_dir)

    with pytest.raises(td.VersionNotFoundError, match="(?i)shallow"):
        td.resolve_version_payload("widget", "1.0.0")


# -- end-to-end against this repo's real history ------------------------------
#
# These read *this* repo's actual git log, so they only prove anything on a
# full-history checkout — skip (not fail) on a shallow one rather than making
# the whole suite depend on the ambient checkout depth of wherever it runs.

_real_repo_root = Path(__file__).resolve().parents[2]
_skip_if_shallow = pytest.mark.skipif(
    td._is_shallow(_real_repo_root),
    reason="shallow checkout — this repo's real version history isn't available",
)

@_skip_if_shallow
def test_resolve_real_microbiome_1_0_0_from_history():
    """The one real version transition that actually exists today (PR #367,
    #286-changed templates bumped 1.0.0 -> 1.1.0) — proves this works against
    real repo history, not just the synthetic fixture above.

    The resolved bodies are genuinely identical: the BIOM/G2 hint additions
    that motivated the bump landed in an *earlier* commit that was still
    tagged 1.0.0 (the version-discipline violation #367 exists to catch), so
    by the time 1.0.0's *last* tagged commit is reached, the content already
    matches 1.1.0 — #367 only moved the version number, not the body. A real,
    correctly-empty diff, not a bug in this tool (which is exactly what an
    earlier draft of this test got wrong: a Windows-only text-decoding bug in
    `_git()` — decoding without explicit encoding="utf-8" — mangled an em-dash
    in one side and not the other, producing a false "notes changed")."""
    old = td.resolve_version_payload("microbiome", "1.0.0")
    new = td.resolve_version_payload("microbiome", "1.1.0")
    assert old["version"] == "1.0.0"
    assert new["version"] == "1.1.0"

    diff = td.semantic_diff(old, new)
    assert diff == {"classes_added": [], "classes_removed": [], "classes_changed": {}}
