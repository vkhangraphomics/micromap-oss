# micromap-mapforge/tests/test_m2_golden.py
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from micromap_mapforge.cli import main
from micromap_mapforge.confidence import Confidence
from micromap_mapforge.resolve.base import Candidate
from tests.golden._diff import diff_file_pair

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"
GOLDEN = HERE / "golden" / "study_tsv_m2"


def _fake_resolvers():
    taxon = MagicMock()
    taxon.label = "Taxon"
    taxon.resolve = MagicMock(side_effect=lambda term, ctx: (
        [Candidate(f"ncbi_tax_id:{term}", Confidence.EXTRACTED, 1.0, "", {}, "ncbi_tax_id", term)] if term.isdigit() else []
    ))
    disease = MagicMock()
    disease.label = "Disease"
    disease.resolve = MagicMock(side_effect=lambda term, ctx: (
        [Candidate("doid:8778", Confidence.INFERRED, 0.95, "", {}, "doid", "8778")] if "crohn" in term.lower() else
        [Candidate("doid:8577", Confidence.INFERRED, 0.95, "", {}, "doid", "8577")] if "colitis" in term.lower() else []
    ))
    empty = MagicMock(resolve=MagicMock(return_value=[]))
    return {
        "Taxon": taxon, "Disease": disease,
        "Metabolite": empty, "Drug": empty, "Gene": empty,
        "Protein": empty, "Pathway": empty, "Paper": empty, "BodySite": empty,
    }


def test_m2_golden_end_to_end(tmp_path):
    out_dir = tmp_path / "out"
    runner = CliRunner()

    # inspect + map (heuristic)
    r = runner.invoke(main, ["map", str(FIXTURES / "study.tsv"),
                             "--out", str(out_dir), "--mode", "heuristic"])
    assert r.exit_code == 0, r.output

    # resolve (mocked resolvers)
    with patch("micromap_mapforge.cli._build_resolvers", return_value=_fake_resolvers()):
        r = runner.invoke(main, ["resolve", "--bundle", str(out_dir),
                                 "--neo4j-uri", "bolt://localhost:7687",
                                 "--neo4j-user", "neo4j", "--neo4j-password", "x"])
        assert r.exit_code == 0, r.output

    # plan
    r = runner.invoke(main, ["plan", "--bundle", str(out_dir),
                             "--organization-id", "test-org"])
    assert r.exit_code == 0, r.output

    # emit
    r = runner.invoke(main, ["emit", "--bundle", str(out_dir)])
    assert r.exit_code == 0, r.output

    # Compare generated cypher + params to golden. Uses tests/golden/_diff.py
    # so a failure renders as a per-key structural diff (JSON) or unified
    # text diff (Cypher) — readable on N-line fixtures (3b-4 / #128).
    for fname in (
        "nodes_Taxon.cypher", "nodes_Taxon.params.json",
        "nodes_Disease.cypher", "nodes_Disease.params.json",
    ):
        diff = diff_file_pair(out_dir / "cypher" / fname, GOLDEN / "cypher" / fname)
        assert not diff, f"{fname} drifted from golden:\n{diff}"
