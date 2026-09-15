# micromap-mapforge/tests/test_m3_golden.py
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from click.testing import CliRunner

from micromap_mapforge.cli import main
from micromap_mapforge.confidence import Confidence
from micromap_mapforge.resolve.base import Candidate


HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"


def _fake_resolvers():
    taxon = MagicMock()
    taxon.label = "Taxon"
    taxon.resolve = MagicMock(side_effect=lambda term, ctx: (
        [Candidate(f"ncbi_tax_id:{term}", Confidence.EXTRACTED, 1.0, "", {},
                   "ncbi_tax_id", term)] if term.isdigit() else []
    ))
    disease = MagicMock()
    disease.label = "Disease"
    disease.resolve = MagicMock(side_effect=lambda term, ctx: (
        [Candidate("doid:8778", Confidence.INFERRED, 0.95, "", {},
                   "doid", "8778")] if "crohn" in term.lower() else
        [Candidate("doid:8577", Confidence.INFERRED, 0.95, "", {},
                   "doid", "8577")] if "colitis" in term.lower() else []
    ))
    empty = MagicMock(resolve=MagicMock(return_value=[]))
    return {
        "Taxon": taxon, "Disease": disease,
        "Metabolite": empty, "Drug": empty, "Gene": empty,
        "Protein": empty, "Pathway": empty, "Paper": empty, "BodySite": empty,
    }


def test_m3_end_to_end_internal_tier_to_core(tmp_path):
    out = tmp_path / "out"
    runner = CliRunner()

    # inspect + map
    r = runner.invoke(main, ["map", str(FIXTURES / "study.tsv"),
                             "--out", str(out), "--mode", "heuristic"])
    assert r.exit_code == 0, r.output

    # drop a contributor.yaml into the bundle
    (out / "contributor.yaml").write_text(
        yaml.safe_dump({"contributor": "graphomics", "tier": "internal"}),
        encoding="utf-8",
    )

    # drop a minimal policy
    policy = tmp_path / "policy.yaml"
    policy.write_text(yaml.safe_dump({
        "rules": [
            {"match": {"tier": "internal"}, "destination": "micromap-core"},
            {"match": {"tier": "external"}, "destination": "registry-only"},
        ],
        "default": {"destination": "registry-only"},
    }), encoding="utf-8")

    # resolve (mocked)
    with patch("micromap_mapforge.cli._build_resolvers", return_value=_fake_resolvers()):
        r = runner.invoke(main, ["resolve", "--bundle", str(out),
                                 "--neo4j-uri", "bolt://localhost", "--neo4j-user", "n",
                                 "--neo4j-password", "x"])
        assert r.exit_code == 0, r.output

    # plan
    r = runner.invoke(main, ["plan", "--bundle", str(out), "--organization-id", "graphomics",
                             "--policy", str(policy)])
    assert r.exit_code == 0, r.output
    routing = yaml.safe_load((out / "routing.yaml").read_text(encoding="utf-8"))
    assert routing["destination"] == "micromap-core"

    # emit
    r = runner.invoke(main, ["emit", "--bundle", str(out)])
    assert r.exit_code == 0, r.output

    # approve
    r = runner.invoke(main, ["approve", "--bundle", str(out), "--reviewer", "alice"])
    assert r.exit_code == 0, r.output
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["approved"] is True
    assert manifest["reviewer"] == "alice"

    # submit (executor + contribution writer both patched — no real Neo4j)
    fake_receipt = MagicMock(success=True, destination="micromap-core",
                             nodes_written=3, relationships_written=0, notes="")
    fake_executor = MagicMock(submit=MagicMock(return_value=fake_receipt))
    fake_contribution = MagicMock()
    with patch("micromap_mapforge.cli._executor_for_bundle", return_value=(fake_executor, None)), \
         patch("micromap_mapforge.cli._write_contribution_for_bundle",
               side_effect=fake_contribution):
        r = runner.invoke(main, ["submit", "--bundle", str(out),
                                 "--neo4j-uri", "bolt://localhost", "--neo4j-user", "n",
                                 "--neo4j-password", "x"])
    assert r.exit_code == 0, r.output
    assert fake_executor.submit.called
    assert fake_contribution.called


def test_m3_end_to_end_pii_overrides_tier_to_registry(tmp_path):
    """Same chain, but sensitivity=pii → registry-only even for tier=internal."""
    out = tmp_path / "out"
    runner = CliRunner()
    r = runner.invoke(main, ["map", str(FIXTURES / "study.tsv"),
                             "--out", str(out), "--mode", "heuristic"])
    assert r.exit_code == 0, r.output
    (out / "contributor.yaml").write_text(
        yaml.safe_dump({"contributor": "graphomics", "tier": "internal", "sensitivity": "pii"}),
        encoding="utf-8",
    )

    policy = tmp_path / "policy.yaml"
    policy.write_text(yaml.safe_dump({
        "rules": [
            {"match": {"sensitivity": "pii"}, "destination": "registry-only"},
            {"match": {"tier": "internal"}, "destination": "micromap-core"},
        ],
        "default": {"destination": "registry-only"},
    }), encoding="utf-8")

    with patch("micromap_mapforge.cli._build_resolvers", return_value=_fake_resolvers()):
        r = runner.invoke(main, ["resolve", "--bundle", str(out),
                                 "--neo4j-uri", "bolt://localhost", "--neo4j-user", "n",
                                 "--neo4j-password", "x"])
        assert r.exit_code == 0, r.output

    r = runner.invoke(main, ["plan", "--bundle", str(out), "--organization-id", "graphomics",
                             "--policy", str(policy)])
    assert r.exit_code == 0, r.output
    routing = yaml.safe_load((out / "routing.yaml").read_text(encoding="utf-8"))
    assert routing["destination"] == "registry-only"
