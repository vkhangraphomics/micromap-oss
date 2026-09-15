"""CLI tests for `mapforge templates list/show` and bare-name --schema-config
acceptance on `mapforge map` (Theme A2' / #74 slice 4)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from micromap_mapforge.cli import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# `mapforge templates list`
# ---------------------------------------------------------------------------


def test_templates_list_succeeds(runner: CliRunner):
    result = runner.invoke(main, ["templates", "list"])
    assert result.exit_code == 0, result.output


def test_templates_list_includes_every_builtin(runner: CliRunner):
    result = runner.invoke(main, ["templates", "list"])
    assert "microbiome" in result.output
    assert "genomics" in result.output
    assert "transcriptomics" in result.output


def test_templates_list_includes_description(runner: CliRunner):
    """Each template line should carry the YAML's `description:` field so the
    output is self-explanatory."""
    result = runner.invoke(main, ["templates", "list"])
    # Microbiome's description starts with "MicroMap microbiome KG ontology"
    # per templates/microbiome.yaml. The CLI may format the line in various
    # ways; the substring check tolerates that.
    assert "microbiome KG ontology" in result.output


# ---------------------------------------------------------------------------
# `mapforge templates show <name>`
# ---------------------------------------------------------------------------


def test_templates_show_emits_valid_yaml(runner: CliRunner):
    result = runner.invoke(main, ["templates", "show", "genomics"])
    assert result.exit_code == 0, result.output
    parsed = yaml.safe_load(result.output)
    assert parsed["name"] == "micromap-genomics"
    assert "Variant" in parsed["classes"]


def test_templates_show_is_case_insensitive(runner: CliRunner):
    result = runner.invoke(main, ["templates", "show", "GENOMICS"])
    assert result.exit_code == 0
    assert "micromap-genomics" in result.output


def test_templates_show_unknown_name_fails_with_helpful_message(
    runner: CliRunner,
):
    result = runner.invoke(main, ["templates", "show", "not-a-real-template"])
    # Exit code 2: Click convention for usage error (bad argument).
    assert result.exit_code == 2, result.output
    # result.output mixes stdout + stderr (Click 8 CliRunner default), so the
    # err=True echo lands here.
    assert "not-a-real-template" in result.output
    assert "microbiome" in result.output   # alternatives listed


# ---------------------------------------------------------------------------
# `mapforge map --schema-config <bare-name>`
# ---------------------------------------------------------------------------


def test_map_accepts_genomics_bare_name(runner: CliRunner, tmp_path: Path):
    """End-to-end smoke: --schema-config genomics produces a bundle with the
    genomics schema_config.yaml copied in."""
    # A minimal CSV so the inspect step succeeds. Use one of the genomics
    # fixture columns so the heuristic mapper produces at least one entity.
    csv = tmp_path / "data.csv"
    csv.write_text(
        "gene_symbol,clinvar_id\nBRCA1,RCV000077444\n",
        encoding="utf-8",
    )
    out = tmp_path / "bundle"

    result = runner.invoke(main, [
        "map",
        str(csv),
        "--out", str(out),
        "--schema-config", "genomics",
    ])
    assert result.exit_code == 0, result.output

    # The bundle's schema_config.yaml must be the genomics one (not microbiome).
    bundle_schema = out / "schema_config.yaml"
    assert bundle_schema.is_file()
    parsed = yaml.safe_load(bundle_schema.read_text(encoding="utf-8"))
    assert parsed["name"] == "micromap-genomics"


def test_map_rejects_unknown_schema_config_name(
    runner: CliRunner, tmp_path: Path,
):
    csv = tmp_path / "data.csv"
    csv.write_text("a,b\n1,2\n", encoding="utf-8")
    out = tmp_path / "bundle"

    result = runner.invoke(main, [
        "map",
        str(csv),
        "--out", str(out),
        "--schema-config", "not-a-real-template",
    ])
    # Exit code 3: schema_config resolution failure (load_schema_config raised).
    # Distinct from Click's exit-code-2 usage errors so a downstream script can
    # tell a typo'd template name apart from a malformed CLI invocation.
    assert result.exit_code == 3, result.output
    # Error message should not double-prefix `schema_config:` (the
    # SchemaConfigError message already carries the prefix).
    assert "error: schema_config: schema_config:" not in result.output
    assert "not-a-real-template" in result.output
    # Available alternatives surfaced.
    assert "microbiome" in result.output


def test_map_rejects_nonexistent_file_path_via_schema_config(
    runner: CliRunner, tmp_path: Path,
):
    """A `--schema-config` value that looks like a file path (contains `/`,
    ends in `.yaml`) but doesn't exist falls through to the bare-name lookup
    and fails there. This pins the current behavior — the error message lists
    template-name alternatives, which can be misleading for a real typo'd
    path, but the contract is documented and a future improvement can replace
    this test rather than removing it silently.
    """
    csv = tmp_path / "data.csv"
    csv.write_text("a,b\n1,2\n", encoding="utf-8")
    out = tmp_path / "bundle"
    bogus = tmp_path / "does-not-exist.yaml"

    result = runner.invoke(main, [
        "map",
        str(csv),
        "--out", str(out),
        "--schema-config", str(bogus),
    ])
    # Currently falls through to the bare-name-not-found path → exit 3.
    assert result.exit_code == 3, result.output
    assert str(bogus) in result.output or "does-not-exist.yaml" in result.output


# ---------------------------------------------------------------------------
# Version surface in CLI (Theme A4 / #74)
# ---------------------------------------------------------------------------


def test_templates_list_includes_version_column(runner: CliRunner):
    """3-column format: name, v<version>, description."""
    result = runner.invoke(main, ["templates", "list"])
    assert result.exit_code == 0, result.output
    # Every template line should contain 'v1.0.0' as the version column.
    assert "v1.0.0" in result.output
    # The em-dash separator from the A2'-slice-4 format is gone.
    assert "—" not in result.output
    # Each line should have at least 3 whitespace-separated tokens
    # (name, v<version>, description-words...). The description token count
    # depends on per-template content; we pin only that the version column
    # exists in position 2 and starts with 'v'.
    for line in result.output.strip().split("\n"):
        if not line.strip():
            continue
        tokens = line.split()
        assert len(tokens) >= 2, f"line missing version column: {line!r}"
        assert tokens[1].startswith("v"), (
            f"second token doesn't start with 'v': {tokens[1]!r}"
        )


def test_templates_list_json_emits_structured_output(runner: CliRunner):
    """--json flag returns parseable JSON; sorted by name; carries
    name/version/description per entry."""
    import json
    result = runner.invoke(main, ["templates", "list", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)
    names = [entry["name"] for entry in data]
    assert names == sorted(names), f"output not sorted by name: {names}"
    for entry in data:
        assert set(entry.keys()) >= {"name", "version", "description"}
        # A version is surfaced per entry; the exact value is pinned by the
        # version-discipline lock (test_template_versioning.py), not here — so this
        # stays green across template version bumps (#152/#153).
        assert re.fullmatch(r"\d+\.\d+\.\d+", entry["version"]), entry
    # Every shipped template present.
    assert {"microbiome", "genomics", "transcriptomics"} <= set(names)


def test_templates_version_subcommand_prints_version(runner: CliRunner):
    """`mapforge templates version <name>` prints just the version."""
    result = runner.invoke(main, ["templates", "version", "genomics"])
    assert result.exit_code == 0, result.output
    # Prints just the version (a semver string); the value is pinned by the lock,
    # so assert the shape, not a literal that changes on every bump (#152/#153).
    assert re.fullmatch(r"\d+\.\d+\.\d+", result.output.strip()), result.output


def test_templates_version_unknown_name_fails_with_alternatives(
    runner: CliRunner,
):
    """Unknown name -> exit 2 with the alternatives list on stderr."""
    result = runner.invoke(main, ["templates", "version", "not-a-real-template"])
    assert result.exit_code == 2, result.output
    assert "not-a-real-template" in result.output
    assert "microbiome" in result.output
