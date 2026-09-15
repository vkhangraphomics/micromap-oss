import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from click.testing import CliRunner

from micromap_mapforge.cli import main


FIXTURES = Path(__file__).parent / "fixtures"


def _fake_resolvers():
    # A minimal resolver registry that resolves tax_id 562 to Taxon NCBI:562
    from micromap_mapforge.confidence import Confidence
    from micromap_mapforge.resolve.base import Candidate
    taxon = MagicMock()
    taxon.label = "Taxon"
    taxon.resolve = MagicMock(side_effect=lambda term, ctx: (
        [Candidate("NCBI:562", Confidence.EXTRACTED, 1.0, "", {})] if term == "562" else []
    ))
    disease = MagicMock()
    disease.label = "Disease"
    disease.resolve = MagicMock(side_effect=lambda term, ctx: (
        [Candidate("DOID:8778", Confidence.INFERRED, 0.95, "", {})] if term == "Crohn's Disease" else []
    ))
    registry = {
        "Taxon": taxon, "Disease": disease,
        "Metabolite": MagicMock(resolve=MagicMock(return_value=[])),
        "Drug": MagicMock(resolve=MagicMock(return_value=[])),
        "Gene": MagicMock(resolve=MagicMock(return_value=[])),
        "Protein": MagicMock(resolve=MagicMock(return_value=[])),
        "Pathway": MagicMock(resolve=MagicMock(return_value=[])),
        "Paper": MagicMock(resolve=MagicMock(return_value=[])),
        "BodySite": MagicMock(resolve=MagicMock(return_value=[])),
    }
    return registry


def test_cli_resolve_writes_resolution_and_unresolved(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    # Seed mapping.yaml from a prior `mapforge map` run
    mapping = {
        "source": {"name": "study", "format": "tsv", "path": str(FIXTURES / "study.tsv")},
        "entities": [
            {"label": "Taxon", "match_on": "ncbi_tax_id",
             "columns": {"ncbi_tax_id": "tax_id"}, "confidence": "EXTRACTED"},
            {"label": "Disease", "match_on": "name_normalized",
             "columns": {"name": "condition"}, "confidence": "EXTRACTED"},
        ],
        "relationships": [],
    }
    (out / "mapping.yaml").write_text(yaml.safe_dump(mapping), encoding="utf-8")

    runner = CliRunner()
    with patch("micromap_mapforge.cli._build_resolvers", return_value=_fake_resolvers()):
        result = runner.invoke(
            main,
            ["resolve", "--bundle", str(out),
             "--neo4j-uri", "bolt://localhost:7687",
             "--neo4j-user", "neo4j", "--neo4j-password", "test"],
        )
    assert result.exit_code == 0, result.output
    assert (out / "resolution.json").exists()
    assert (out / "unresolved.md").exists()
    data = json.loads((out / "resolution.json").read_text(encoding="utf-8"))
    assert data["resolved_count"] >= 1


def test_cli_resolve_resolves_relative_source_path_against_bundle(tmp_path):
    """#116: source.path that is relative must be resolved against the bundle dir.

    Mirrors test_write_contribution_resolves_relative_source_path for resolve_cmd.
    The shipped examples/disbiome/mapping.yaml uses 'disbiome_sample.csv'
    (relative-to-bundle); without the fix, _load_rows opens it relative to
    CWD and fails with FileNotFoundError.
    """
    out = tmp_path / "out"
    out.mkdir()
    # Source file lives in the bundle directory; mapping references it relatively.
    (out / "study.tsv").write_text("tax_id\tcondition\n562\tCrohn's Disease\n", encoding="utf-8")
    mapping = {
        "source": {"name": "study", "format": "tsv", "path": "study.tsv"},  # relative
        "entities": [
            {"label": "Taxon", "match_on": "ncbi_tax_id",
             "columns": {"ncbi_tax_id": "tax_id"}, "confidence": "EXTRACTED"},
        ],
        "relationships": [],
    }
    (out / "mapping.yaml").write_text(yaml.safe_dump(mapping), encoding="utf-8")

    runner = CliRunner()
    with patch("micromap_mapforge.cli._build_resolvers", return_value=_fake_resolvers()):
        result = runner.invoke(
            main,
            ["resolve", "--bundle", str(out),
             "--neo4j-uri", "bolt://localhost:7687",
             "--neo4j-user", "neo4j", "--neo4j-password", "test"],
        )
    assert result.exit_code == 0, result.output
    assert "FileNotFoundError" not in result.output
    data = json.loads((out / "resolution.json").read_text(encoding="utf-8"))
    assert data["resolved_count"] >= 1, (
        f"expected at least one resolved entity (the seeded row's tax_id 562); "
        f"got resolved={data['resolved_count']}, unresolved={data['unresolved_count']}"
    )


def test_cli_plan_writes_routing_yaml(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    mapping = {"source": {"name": "s", "format": "tsv", "path": "s.tsv"}, "entities": [], "relationships": []}
    (out / "mapping.yaml").write_text(yaml.safe_dump(mapping), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        main, ["plan", "--bundle", str(out), "--organization-id", "partner-xyz"]
    )
    assert result.exit_code == 0, result.output
    routing_path = out / "routing.yaml"
    assert routing_path.exists()
    routing = yaml.safe_load(routing_path.read_text(encoding="utf-8"))
    assert routing["destination"] == "micromap-core"
    assert routing["organization_id"] == "partner-xyz"


def test_cli_plan_override(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "mapping.yaml").write_text(
        yaml.safe_dump({"source": {"name": "s", "format": "tsv", "path": "s.tsv"},
                        "entities": [], "relationships": []}),
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(
        main, ["plan", "--bundle", str(out),
               "--organization-id", "o", "--destination", "registry-only"]
    )
    assert result.exit_code == 0, result.output
    routing = yaml.safe_load((out / "routing.yaml").read_text(encoding="utf-8"))
    assert routing["destination"] == "registry-only"
