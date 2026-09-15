from pathlib import Path

from click.testing import CliRunner

from micromap_mapforge.cli import main
from tests.golden._diff import diff_yaml

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"
GOLDEN = HERE / "golden"


def test_golden_heuristic_study_tsv(tmp_path: Path):
    out_dir = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "map",
            str(FIXTURES / "study.tsv"),
            "--out", str(out_dir),
            "--mode", "heuristic",
        ],
    )
    assert result.exit_code == 0, result.output

    generated = (out_dir / "mapping.yaml").read_text(encoding="utf-8")
    expected = (GOLDEN / "study_tsv" / "mapping.yaml").read_text(encoding="utf-8")

    # Normalize the 'path' field (absolute) out before diffing; the path
    # depends on tmp_path and the test fixture's installation location.
    def _normalize(text: str) -> str:
        return "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("path:")
        )

    diff = diff_yaml(_normalize(generated), _normalize(expected))
    assert not diff, f"mapping.yaml drifted from golden:\n{diff}"
