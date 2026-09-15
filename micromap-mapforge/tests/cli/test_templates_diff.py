"""CLI tests for `mapforge templates diff <name> --from <ver> --to <ver>` (#152).

Exercises the real repo's one actual version transition (microbiome 1.0.0 ->
1.1.0, PR #367) for the git-history path, plus a synthetic fixture (monkeypatched
TEMPLATES_DIR) for the output-formatting path — the real transition happens to
be a "no changes" case (the content that motivated the bump landed in an
earlier still-1.0.0-tagged commit; #367 only moved the version number), so it
can't exercise the added/changed formatting on its own.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from micromap_mapforge.cli import main
from micromap_mapforge.mapping import template_diff as td


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# These exercise a version that only exists pre-#367, i.e. real git history —
# skip (not fail) on a shallow checkout rather than depending on the ambient
# checkout depth of wherever the suite runs (see test_template_diff.py).
_skip_if_shallow = pytest.mark.skipif(
    td._is_shallow(Path(__file__).resolve().parents[2]),
    reason="shallow checkout — this repo's real version history isn't available",
)


def test_templates_diff_same_version_shows_no_changes(runner: CliRunner):
    result = runner.invoke(main, ["templates", "diff", "genomics", "--from", "1.1.0", "--to", "1.1.0"])
    assert result.exit_code == 0, result.output
    assert "no changes" in result.output


@_skip_if_shallow
def test_templates_diff_real_transition_via_git_history(runner: CliRunner):
    """microbiome's one real version bump: resolving 1.0.0 goes through the
    git-history walk (unlike the same-version test above, which never leaves
    the live-file fast path) and correctly finds no semantic difference from
    1.1.0 — the honest result, not a bug (see test_template_diff.py for the
    full story, including the encoding bug an earlier draft of this test
    tripped over)."""
    result = runner.invoke(main, ["templates", "diff", "microbiome", "--from", "1.0.0", "--to", "1.1.0"])
    assert result.exit_code == 0, result.output
    assert "microbiome: 1.0.0 -> 1.1.0" in result.output
    assert "no changes" in result.output


def test_templates_diff_prints_added_class_and_identifiers(runner: CliRunner, tmp_path, monkeypatch):
    """A synthetic fixture (not real history) for the output-formatting path:
    a genuinely added class plus an identifier addition, printed in full."""
    repo = tmp_path / "repo"
    templates_dir = repo / "micromap_mapforge" / "mapping" / "templates"
    templates_dir.mkdir(parents=True)

    def run(*args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)

    run("init", "-q")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "test")

    tmpl = templates_dir / "widget.yaml"
    tmpl.write_text(
        'version: "1.0.0"\n'
        "classes:\n"
        "  Thing:\n"
        "    x_mapforge:\n"
        "      identifiers: [thing_id]\n",
        encoding="utf-8",
    )
    run("add", "-A")
    run("commit", "-q", "-m", "v1.0.0")

    tmpl.write_text(
        'version: "1.1.0"\n'
        "classes:\n"
        "  Thing:\n"
        "    x_mapforge:\n"
        "      identifiers: [thing_id, thing_sku]\n"
        "  Gadget: {}\n",
        encoding="utf-8",
    )
    run("add", "-A")
    run("commit", "-q", "-m", "v1.1.0")

    monkeypatch.setattr(td, "TEMPLATES_DIR", templates_dir)

    result = runner.invoke(main, ["templates", "diff", "widget", "--from", "1.0.0", "--to", "1.1.0"])
    assert result.exit_code == 0, result.output
    assert "classes added: Gadget" in result.output
    assert "Thing:" in result.output
    assert "+ identifiers: thing_sku" in result.output


def test_templates_diff_unknown_version_fails_with_helpful_message(runner: CliRunner):
    result = runner.invoke(main, ["templates", "diff", "microbiome", "--from", "9.9.9", "--to", "1.1.0"])
    assert result.exit_code == 2, result.output
    assert "9.9.9" in result.output
    assert "microbiome" in result.output


def test_templates_diff_unknown_template_fails_with_helpful_message(runner: CliRunner):
    result = runner.invoke(main, ["templates", "diff", "not-a-real-template", "--from", "1.0.0", "--to", "1.1.0"])
    assert result.exit_code == 2, result.output
    assert "not-a-real-template" in result.output


@_skip_if_shallow
def test_templates_diff_json_emits_structured_output(runner: CliRunner):
    result = runner.invoke(main, [
        "templates", "diff", "microbiome", "--from", "1.0.0", "--to", "1.1.0", "--json",
    ])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data == {"classes_added": [], "classes_removed": [], "classes_changed": {}}
