import json
from pathlib import Path

from click.testing import CliRunner

from micromap_mapforge.cli import main

FIXTURES = Path(__file__).parent / "fixtures"


def test_cli_inspect_csv_writes_report(tmp_path: Path):
    out_dir = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["inspect", str(FIXTURES / "study.csv"), "--out", str(out_dir)],
    )
    assert result.exit_code == 0, result.output

    report = out_dir / "inspection-report.md"
    assert report.exists()
    text = report.read_text(encoding="utf-8")
    assert "study.csv" in text
    assert "tax_id" in text
    assert "| integer" in text  # type-inference result in the table

    profile_json = out_dir / "inspection.json"
    assert profile_json.exists()
    data = json.loads(profile_json.read_text(encoding="utf-8"))
    assert data["format"] == "csv"


def test_cli_inspect_unsupported_extension_errors(tmp_path: Path):
    mystery = tmp_path / "mystery.xyz"
    mystery.write_text("nope")
    runner = CliRunner()
    result = runner.invoke(
        main, ["inspect", str(mystery), "--out", str(tmp_path / "out")]
    )
    assert result.exit_code != 0
    assert "unsupported file extension" in result.output.lower()


from unittest.mock import patch, MagicMock


def test_cli_map_heuristic_writes_mapping(tmp_path: Path):
    out_dir = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["map", str(FIXTURES / "study.tsv"), "--out", str(out_dir), "--mode", "heuristic"],
    )
    assert result.exit_code == 0, result.output

    mapping_yaml = out_dir / "mapping.yaml"
    assert mapping_yaml.exists()

    import yaml
    mapping = yaml.safe_load(mapping_yaml.read_text(encoding="utf-8"))
    assert mapping["source"]["format"] == "tsv"
    labels = [e["label"] for e in mapping["entities"]]
    assert "Taxon" in labels


def test_cli_map_llm_mode_uses_client_factory(tmp_path: Path):
    out_dir = tmp_path / "out"

    fake_mapping_yaml = """
source:
  name: study
  format: tsv
  path: study.tsv
entities:
  - label: Taxon
    match_on: ncbi_tax_id
    columns:
      ncbi_tax_id: tax_id
    confidence: EXTRACTED
relationships: []
"""
    fake_client = MagicMock()
    fake_message = MagicMock()
    fake_message.content = [MagicMock(text=fake_mapping_yaml.strip())]
    fake_client.messages.create.return_value = fake_message

    runner = CliRunner()
    with patch(
        "micromap_mapforge.cli._build_anthropic_client", return_value=fake_client
    ):
        result = runner.invoke(
            main,
            [
                "map",
                str(FIXTURES / "study.tsv"),
                "--out", str(out_dir),
                "--mode", "llm",
                "--hint", "gut microbiome disease association study",
            ],
        )
    assert result.exit_code == 0, result.output
    assert (out_dir / "mapping.yaml").exists()
    assert fake_client.messages.create.called
