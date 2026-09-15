"""Decision provenance on submit (#190): the :Decision{data_contribution} that
`mapforge submit` records over a contribution, and the non-gating guarantee."""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from click.testing import CliRunner

from micromap_mapforge.cli import main
from micromap_mapforge.provenance.decision import DecisionRecord, cypher_for_decision


# --- unit: the Decision Cypher --------------------------------------------

def test_cypher_for_decision_structure_and_param_safety():
    rec = DecisionRecord(
        id="dec-contrib-abc123",
        organization_id="demo",
        occurred_at=datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc),
        # A hostile summary must never fragment a statement — it stays in params.
        summary="evil'; DETACH DELETE n; //",
        entity_tags=["parkinson disease", "NCBITaxon:853"],
        about_resolved=[{"label": "Disease", "field": "name_normalized", "value": "parkinson disease"}],
        contribution_mapping_sha256="map-sha",
        contribution_source_sha256="src-sha",
        resolved_count=205,
    )
    stmts = cypher_for_decision(rec)
    joined = " ".join(s for s, _ in stmts)

    # The four expected statements are present.
    assert "MERGE (d:Decision {id: $id})" in joined
    assert "MERGE (d)-[:RECORDS]->(c)" in joined          # decision -> execution provenance
    assert joined.count("MERGE (d)-[:ABOUT]->(e)") == 2   # curated tags + resolved subjects

    # Injection safety: no value is interpolated into any Cypher string.
    for stmt, params in stmts:
        assert "DETACH DELETE" not in stmt
        assert "parkinson disease" not in stmt
        assert "NCBITaxon:853" not in stmt
    all_params = {k: v for _, p in stmts for k, v in p.items()}
    assert all_params["summary"] == "evil'; DETACH DELETE n; //"
    assert all_params["entity_tags"] == ["parkinson disease", "NCBITaxon:853"]


def test_cypher_for_decision_omits_records_without_contribution_keys():
    rec = DecisionRecord(
        id="dec-x", organization_id="demo",
        occurred_at=datetime(2026, 6, 8, tzinfo=timezone.utc), summary="s",
    )
    joined = " ".join(s for s, _ in cypher_for_decision(rec))
    assert "MERGE (d:Decision" in joined
    assert ":RECORDS" not in joined   # no contribution keys -> no RECORDS edge
    assert ":ABOUT" not in joined     # no tags / resolved -> no ABOUT edges


# --- integration: submit wiring + non-gating -------------------------------

def _seed(tmp_path: Path) -> Path:
    out = tmp_path / "out"
    out.mkdir()
    (out / "mapping.yaml").write_text(yaml.safe_dump({
        "source": {"name": "pd-panel", "format": "tsv", "path": str(tmp_path / "s.tsv")},
        "entities": [{"label": "Taxon", "match_on": "ncbi_tax_id",
                      "columns": {"ncbi_tax_id": "tax_id"}, "confidence": "EXTRACTED"}],
        "relationships": [],
    }), encoding="utf-8")
    (out / "routing.yaml").write_text(yaml.safe_dump({
        "destination": "micromap-core", "organization_id": "demo",
        "provenance": {"contributor": "graphomics", "submitted_at": "now",
                       "mapping_version": "sha256:abc"},
        "federation_endpoint": {"type": None, "uri": None},
    }), encoding="utf-8")
    (out / "resolution.json").write_text(json.dumps({
        "resolved": [], "unresolved": [], "ambiguous": [],
        "resolved_count": 0, "unresolved_count": 0, "ambiguous_count": 0,
    }), encoding="utf-8")
    (tmp_path / "s.tsv").write_text("tax_id\n562\n", encoding="utf-8")
    return out


def _fakes():
    receipt = MagicMock(success=True, destination="micromap-core",
                        nodes_written=1, relationships_written=0, notes="")
    executor = MagicMock(submit=MagicMock(return_value=receipt))
    return executor


def test_submit_records_decision_with_about_tags(tmp_path):
    bundle_dir = _seed(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["emit", "--bundle", str(bundle_dir)])
    executor = _fakes()

    with patch("micromap_mapforge.cli._executor_for_bundle", return_value=(executor, None)), \
         patch("micromap_mapforge.cli._write_contribution_for_bundle"), \
         patch("micromap_mapforge.cli._write_decision_for_bundle", return_value=2) as dec:
        result = runner.invoke(main, [
            "submit", "--bundle", str(bundle_dir), "--force", "--reviewer", "alice",
            "--neo4j-uri", "bolt://localhost", "--neo4j-user", "u", "--neo4j-password", "p",
            "--about", "parkinson disease,NCBITaxon:853",
        ])
    assert result.exit_code == 0, result.output
    assert dec.called
    # comma-separated --about is split into canonical tags
    assert dec.call_args.kwargs["about_tags"] == ["parkinson disease", "NCBITaxon:853"]
    assert "recorded Decision" in result.output


def test_submit_decision_is_non_gating(tmp_path):
    """A decision-write failure must NOT fail the submit — the data + Contribution
    are already written. Provenance never gates a push to a MapForge KG."""
    bundle_dir = _seed(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["emit", "--bundle", str(bundle_dir)])
    executor = _fakes()

    with patch("micromap_mapforge.cli._executor_for_bundle", return_value=(executor, None)), \
         patch("micromap_mapforge.cli._write_contribution_for_bundle") as contrib, \
         patch("micromap_mapforge.cli._write_decision_for_bundle",
               side_effect=RuntimeError("neo4j down")):
        result = runner.invoke(main, [
            "submit", "--bundle", str(bundle_dir), "--force", "--reviewer", "alice",
            "--neo4j-uri", "bolt://localhost", "--neo4j-user", "u", "--neo4j-password", "p",
        ])
    assert result.exit_code == 0, result.output          # submit still succeeds
    assert executor.submit.called                         # data was written
    assert contrib.called                                 # contribution was written
    assert "decision provenance not recorded" in result.output
    assert "submission stands" in result.output


def test_submit_no_decision_flag_skips_it(tmp_path):
    bundle_dir = _seed(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["emit", "--bundle", str(bundle_dir)])
    executor = _fakes()

    with patch("micromap_mapforge.cli._executor_for_bundle", return_value=(executor, None)), \
         patch("micromap_mapforge.cli._write_contribution_for_bundle"), \
         patch("micromap_mapforge.cli._write_decision_for_bundle") as dec:
        result = runner.invoke(main, [
            "submit", "--bundle", str(bundle_dir), "--force", "--reviewer", "alice",
            "--neo4j-uri", "bolt://localhost", "--neo4j-user", "u", "--neo4j-password", "p",
            "--no-decision",
        ])
    assert result.exit_code == 0, result.output
    assert not dec.called
