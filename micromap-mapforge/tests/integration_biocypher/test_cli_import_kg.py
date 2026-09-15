"""Tests for `mapforge import-kg <adapter>`."""

from pathlib import Path

import yaml
from click.testing import CliRunner

from micromap_mapforge.cli import main


# Reuse FakeGtdbAdapter from the runner test module.
ADAPTER_DOTTED = "tests.integration_biocypher.test_runner:FakeGtdbAdapter"


def test_import_kg_writes_bundle_to_out_dir(tmp_path: Path):
    src = tmp_path / "fake.tsv"
    src.write_text("ignored", encoding="utf-8")
    out = tmp_path / "out"

    runner = CliRunner()
    result = runner.invoke(main, [
        "import-kg", ADAPTER_DOTTED,
        "--source", str(src),
        "--organization-id", "org-test",
        "--out", str(out),
    ])
    assert result.exit_code == 0, result.output

    # mapping.yaml lives at the bundle root and uses schema_adapter format.
    mapping = yaml.safe_load((out / "mapping.yaml").read_text(encoding="utf-8"))
    assert mapping["source"]["format"] == "schema_adapter"

    # Cypher per label exists.
    assert (out / "cypher" / "nodes_OrganismTaxon.cypher").exists()


def test_import_kg_adopt_mode_produces_bundle_ir_json(tmp_path: Path):
    src = tmp_path / "fake.tsv"
    src.write_text("ignored", encoding="utf-8")
    out = tmp_path / "out"

    runner = CliRunner()
    result = runner.invoke(main, [
        "import-kg", ADAPTER_DOTTED,
        "--source", str(src),
        "--organization-id", "org-test",
        "--out", str(out),
    ])
    assert result.exit_code == 0, result.output
    assert (out / "bundle.ir.json").exists()
    assert "nodes=1 edges=1" in result.output


def test_import_kg_rejects_unknown_adapter_path(tmp_path: Path):
    src = tmp_path / "fake.tsv"
    src.write_text("ignored", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(main, [
        "import-kg", "nonexistent.module:Thing",
        "--source", str(src),
        "--organization-id", "org-test",
        "--out", str(tmp_path / "out"),
    ])
    assert result.exit_code != 0
    assert "cannot import" in result.output


def test_import_kg_transform_mode_still_rejected(tmp_path: Path):
    """transform (multi-adapter chaining) stays deferred — adapt shipped
    (#76 C7), transform is spun off separately."""
    src = tmp_path / "fake.tsv"
    src.write_text("ignored", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(main, [
        "import-kg", ADAPTER_DOTTED,
        "--source", str(src),
        "--organization-id", "org-test",
        "--out", str(tmp_path / "out"),
        "--schema-mode", "transform",
    ])
    assert result.exit_code != 0
    assert "transform" in result.output


def test_import_kg_adapt_mode_requires_schema_config(tmp_path: Path):
    src = tmp_path / "fake.tsv"
    src.write_text("ignored", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(main, [
        "import-kg", ADAPTER_DOTTED,
        "--source", str(src),
        "--organization-id", "org-test",
        "--out", str(tmp_path / "out"),
        "--schema-mode", "adapt",
    ])
    assert result.exit_code != 0
    assert "--schema-config" in result.output


def _write_project_schema_config(tmp_path: Path) -> Path:
    """Maps FakeGtdbAdapter's raw labels (OrganismTaxon, MEMBER_OF) onto
    project class names via input_label."""
    path = tmp_path / "schema_config.yaml"
    path.write_text(
        "name: test-project\n"
        'version: "1.0.0"\n'
        "classes:\n"
        "  Taxon:\n"
        "    input_label: OrganismTaxon\n"
        "  HAS_PARENT:\n"
        "    input_label: MEMBER_OF\n",
        encoding="utf-8",
    )
    return path


def test_import_kg_adapt_mode_remaps_labels(tmp_path: Path):
    src = tmp_path / "fake.tsv"
    src.write_text("ignored", encoding="utf-8")
    out = tmp_path / "out"
    schema_config = _write_project_schema_config(tmp_path)

    runner = CliRunner()
    result = runner.invoke(main, [
        "import-kg", ADAPTER_DOTTED,
        "--source", str(src),
        "--organization-id", "org-test",
        "--out", str(out),
        "--schema-mode", "adapt",
        "--schema-config", str(schema_config),
    ])
    assert result.exit_code == 0, result.output

    # The bundle carries the project's schema_config, not the adapter's own.
    mapping = yaml.safe_load((out / "mapping.yaml").read_text(encoding="utf-8"))
    assert mapping["source"]["format"] == "schema_adapter"

    # Adapter's raw "OrganismTaxon" label was remapped to the project's "Taxon".
    assert (out / "cypher" / "nodes_Taxon.cypher").exists()
    assert not (out / "cypher" / "nodes_OrganismTaxon.cypher").exists()


def test_import_kg_adapt_mode_rejects_unmapped_label(tmp_path: Path):
    """FakeGtdbAdapter emits MEMBER_OF edges; a project schema_config that
    only maps the node label (not the edge type) must fail loudly, not
    silently drop the unmapped edge type through."""
    src = tmp_path / "fake.tsv"
    src.write_text("ignored", encoding="utf-8")
    schema_config = tmp_path / "schema_config.yaml"
    schema_config.write_text(
        "name: test-project\n"
        'version: "1.0.0"\n'
        "classes:\n"
        "  Taxon:\n"
        "    input_label: OrganismTaxon\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(main, [
        "import-kg", ADAPTER_DOTTED,
        "--source", str(src),
        "--organization-id", "org-test",
        "--out", str(tmp_path / "out"),
        "--schema-mode", "adapt",
        "--schema-config", str(schema_config),
    ])
    assert result.exit_code != 0
    assert "MEMBER_OF" in result.output


# -- Review finding #7: TypeError from an adapter's __init__ body must NOT
# be swallowed by the no-arg fallback. Real adapter bugs should surface as
# their original TypeError, not be silently retried as cls() and produce
# misleading "missing required argument" or empty-bundle errors.

# Module-level so the dotted-path loader can resolve it.
class AdapterWithBuggyInit:
    """A path-taking adapter whose __init__ body raises TypeError.

    Mimics a real-world bug: a developer typo or wrong-type internal call
    inside an otherwise-valid `def __init__(self, path)`. The CLI must NOT
    silently fall back to `cls()` — that masks the real bug.
    """
    name = "buggy-init"
    schema_config = {"prefixes": {}}

    def __init__(self, path):
        # Deliberately raise TypeError inside the body.
        raise TypeError("simulated internal type error from adapter __init__")

    def get_nodes(self):
        return iter(())

    def get_edges(self):
        return iter(())


def test_import_kg_does_not_swallow_typeerror_from_adapter_init(tmp_path: Path):
    """Regression: review finding #7. Previously this TypeError was caught by
    a bare `except TypeError` and retried as `cls()` — silently masking the
    real adapter bug. The fix uses inspect.signature to decide whether to
    pass `path`; real TypeErrors from inside __init__ now propagate."""
    src = tmp_path / "fake.tsv"
    src.write_text("ignored", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(main, [
        "import-kg",
        "tests.integration_biocypher.test_cli_import_kg:AdapterWithBuggyInit",
        "--source", str(src),
        "--organization-id", "org-test",
        "--out", str(tmp_path / "out"),
    ])
    # Click's CliRunner captures the exception; result.exit_code is non-zero
    # and result.exception is the original TypeError — NOT a downstream
    # "missing required argument" or empty-bundle error.
    assert result.exit_code != 0
    assert isinstance(result.exception, TypeError)
    assert "simulated internal type error" in str(result.exception)
