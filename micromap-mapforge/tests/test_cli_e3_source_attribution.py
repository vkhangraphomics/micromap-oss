"""CLI integration test for E3 source attribution threading (#78).

Tests that _write_contribution_for_bundle() reads e3 source-attribution
fields from mapping.yaml::source and passes them to ContributionRecord.

Uses the same patching pattern as test_cli_submit_m3.py's
test_write_contribution_resolves_relative_source_path -- directly
invoking _write_contribution_for_bundle with a pre-seeded bundle and
patching the inner write_contribution (the Neo4j-side writer) to capture
the ContributionRecord without needing a live database.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from micromap_mapforge.cli import _write_contribution_for_bundle


def _seed_e3_bundle(tmp_path: Path) -> Path:
    """Create a minimal bundle with e3 source-attribution fields in mapping.yaml."""
    out = tmp_path / "bundle"
    out.mkdir()

    # Source file (content doesn't matter — sha256 is computed from it).
    source_file = out / "data.tsv"
    source_file.write_text("tax_id\n562\n", encoding="utf-8")

    # mapping.yaml with all 7 e3 source-attribution fields.
    mapping = {
        "source": {
            "name": "reactome-pathways",
            "format": "tsv",
            "path": "data.tsv",      # bundle-relative — resolved by the CLI
            "license": "CC-BY-4.0",
            "url": "https://reactome.org/download-data",
            "doi": "10.1093/nar/gkx1132",
            "contact": "help@reactome.org",
            "version": "v89",
            "accessed_at": "2026-06-06",
            "ethics_ref": "IRB-2024-0428",
        },
        "entities": [],
        "relationships": [],
    }
    (out / "mapping.yaml").write_text(yaml.safe_dump(mapping), encoding="utf-8")

    # resolution.json with zero counts (no rows to resolve).
    resolution = {
        "resolved": [],
        "unresolved": [],
        "ambiguous": [],
        "resolved_count": 0,
        "unresolved_count": 0,
        "ambiguous_count": 0,
    }
    (out / "resolution.json").write_text(json.dumps(resolution), encoding="utf-8")

    return out


def test_submit_threads_source_attribution_into_contribution_record(tmp_path):
    """_write_contribution_for_bundle passes e3 source-attribution fields
    from mapping.yaml::source into the ContributionRecord.

    Monkey-patches write_contribution (the Neo4j-side writer) so the test
    doesn't need a live database. The captured ContributionRecord is the
    source of truth for the threading contract.
    """
    bundle_dir = _seed_e3_bundle(tmp_path)

    routing = {
        "destination": "registry-only",
        "organization_id": "org-e3-test",
        "provenance": {"contributor": "org-e3-test"},
    }
    fake_receipt = MagicMock(
        success=True,
        destination="registry-only",
        nodes_written=0,
        relationships_written=0,
        notes="",
    )

    captured: list = []

    def _capture_write(driver, record, database="neo4j"):
        captured.append(record)

    fake_driver = MagicMock()
    # Patch target note: `write_contribution` is imported INSIDE the function
    # body of `_write_contribution_for_bundle` (cli.py), not at module top.
    # That means `micromap_mapforge.cli.write_contribution` does NOT exist as
    # a module attribute, so the conventional "patch where it's used" pattern
    # doesn't apply here -- we must patch where the name lives, on the
    # provenance.contribution module. If a future refactor hoists the import
    # to cli.py module-top, this patch target should move to
    # `micromap_mapforge.cli.write_contribution`.
    with patch("neo4j.GraphDatabase.driver", return_value=fake_driver), \
         patch("micromap_mapforge.provenance.contribution.write_contribution",
               side_effect=_capture_write):
        _write_contribution_for_bundle(
            bundle_dir=bundle_dir,
            routing=routing,
            reviewer="alice",
            receipt=fake_receipt,
            neo4j_uri="bolt://localhost",
            neo4j_user="u",
            neo4j_password="p",
            neo4j_database="neo4j",
        )

    assert captured, "ContributionRecord was never written"
    record = captured[0]

    # All 7 E3-new schema fields are threaded through.
    assert record.source_license == "CC-BY-4.0"
    assert record.source_url == "https://reactome.org/download-data"
    assert record.source_doi == "10.1093/nar/gkx1132"
    assert record.source_contact == "help@reactome.org"
    assert record.source_version == "v89"
    assert record.source_accessed_at == "2026-06-06"
    assert record.source_ethics_ref == "IRB-2024-0428"
    # Plus source_name (predates E3 as a mapping.source field, now also
    # threaded onto the :Contribution node alongside the new fields).
    assert record.source_name == "reactome-pathways"
