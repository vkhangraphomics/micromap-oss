"""CLI integration tests for E4 force_submitted provenance stamp (#78).

Tests directly invoke `_write_contribution_for_bundle` with the new
force_submitted kwarg and patch `write_contribution` (Neo4j-side writer)
to capture the ContributionRecord the helper hands off. Mirrors the
pattern established by test_cli_e3_source_attribution.py.

The submit_cmd derivation logic (`True if (not approved and force) else
None`) is one line and covered by spec correctness rather than a
separate test layer -- direct-helper invocation here is cleaner and
faster while exercising the same threading contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from micromap_mapforge.cli import _write_contribution_for_bundle


def _seed_post_e3_bundle(tmp_path: Path) -> tuple[Path, dict]:
    """Build a minimal bundle that _write_contribution_for_bundle can read.

    Mirrors the seed pattern in test_cli_e3_source_attribution.py: write
    mapping.yaml, resolution.json, and a source file so the helper has
    all the artifacts it needs to construct a ContributionRecord. The
    routing dict is returned alongside (the helper takes routing as a
    direct arg rather than reading it from the bundle).
    """
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    # Source file (so source_sha can be computed).
    src = bundle_dir / "data.csv"
    src.write_text("gene_symbol\nBRCA1\n", encoding="utf-8")

    # mapping.yaml: minimal valid shape post-E5 + E3 (sha256 sealed).
    import hashlib
    src_sha = hashlib.sha256(src.read_bytes()).hexdigest()
    mapping = {
        "source": {
            "name": "data",
            "format": "csv",
            "path": "data.csv",
            "sha256": src_sha,
        },
        "entities": [{
            "label": "Gene",
            "match_on": "symbol",
            "columns": {"symbol": "gene_symbol"},
        }],
        "relationships": [],
    }
    (bundle_dir / "mapping.yaml").write_text(
        yaml.safe_dump(mapping), encoding="utf-8",
    )

    # resolution.json: minimal valid shape.
    (bundle_dir / "resolution.json").write_text(
        json.dumps({
            "resolved_count": 1,
            "unresolved_count": 0,
            "ambiguous_count": 0,
        }),
        encoding="utf-8",
    )

    # Minimal routing dict.
    routing = {
        "organization_id": "test-org",
        "provenance": {"enabled": True, "contributor": "test-org"},
    }

    return bundle_dir, routing


def _fake_receipt():
    """A minimal SubmissionReceipt-shaped object."""
    receipt = MagicMock()
    receipt.destination = "micromap-core"
    receipt.success = True
    return receipt


def test_submit_force_stamps_force_submitted_true(tmp_path):
    """When _write_contribution_for_bundle is called with force_submitted=
    True (which submit_cmd derives from `not approved and force`), the
    captured ContributionRecord has force_submitted == True.

    Pins the bypass-stamp contract.
    """
    bundle_dir, routing = _seed_post_e3_bundle(tmp_path)
    captured: list = []

    def _capture_write(driver, record, database="neo4j"):
        captured.append(record)

    fake_driver = MagicMock()
    # Patch target note: write_contribution is imported INSIDE
    # _write_contribution_for_bundle's function body, not at module top.
    # See test_cli_e3_source_attribution.py for the explanation.
    with patch("neo4j.GraphDatabase.driver", return_value=fake_driver), \
         patch("micromap_mapforge.provenance.contribution.write_contribution",
               side_effect=_capture_write):
        _write_contribution_for_bundle(
            bundle_dir=bundle_dir,
            routing=routing,
            reviewer="alice",
            receipt=_fake_receipt(),
            neo4j_uri="bolt://localhost",
            neo4j_user="u",
            neo4j_password="p",
            neo4j_database="neo4j",
            force_submitted=True,
        )

    assert captured, "ContributionRecord was never written"
    assert captured[0].force_submitted is True


def test_submit_approved_does_not_stamp_force_submitted(tmp_path):
    """When _write_contribution_for_bundle is called with force_submitted=
    None (which submit_cmd derives when the bundle was approved, with or
    without --force flag), the captured ContributionRecord has
    force_submitted is None.

    Pins the 'actual bypass' semantics: --force on an already-approved
    bundle does NOT stamp.
    """
    bundle_dir, routing = _seed_post_e3_bundle(tmp_path)
    captured: list = []

    def _capture_write(driver, record, database="neo4j"):
        captured.append(record)

    fake_driver = MagicMock()
    with patch("neo4j.GraphDatabase.driver", return_value=fake_driver), \
         patch("micromap_mapforge.provenance.contribution.write_contribution",
               side_effect=_capture_write):
        _write_contribution_for_bundle(
            bundle_dir=bundle_dir,
            routing=routing,
            reviewer="alice",
            receipt=_fake_receipt(),
            neo4j_uri="bolt://localhost",
            neo4j_user="u",
            neo4j_password="p",
            neo4j_database="neo4j",
            force_submitted=None,
        )

    assert captured, "ContributionRecord was never written"
    assert captured[0].force_submitted is None
