"""
Reconciliation of the two §4 fixes from the closed Increment-B line (#201)
onto the canonical flat PROV-N ingest (`api/routes/provenance_decisions.py`).

Both were silently dropped on `main` and masked by curated demo data:

1. `decision_outcome` — the verdict (supported/refuted/pass/fail/…) was absent
   from the `:Decision` MERGE and from every query-result model, so a
   hypothesis_verdict's actual outcome was lost end-to-end.
2. Disease `:ABOUT` linking matched on `toLower(tag)` instead of
   `normalize_disease_name(tag)`, so "Crohn's Disease" / "IBD" never matched
   the graph's `name_normalized` keys ("crohns disease" /
   "inflammatory bowel disease"). The ledger join under-counted.

These tests pin the fixed behavior so neither can silently regress.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.routes.provenance_decisions import (
    CitedDecision,
    DecisionEvent,
    LedgerEntry,
)
from database.ingestion.base_loader import normalize_disease_name


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def demo_api_key():
    return "demo-key-123"


@pytest.fixture
def mock_kg():
    kg = MagicMock()
    kg.execute_cypher = MagicMock(return_value=[])
    return kg


# --- Fix 1: decision_outcome end-to-end -----------------------------------

def test_decision_outcome_is_a_model_field():
    """The flat contract carries decision_outcome (flat + §4 passthrough)."""
    flat = DecisionEvent(
        id="dec-1", tool="nexus", action_type="hypothesis_verdict",
        occurred_at="2026-06-09T00:00:00Z", summary="s",
        decision_outcome="refuted",
    )
    assert flat.decision_outcome == "refuted"

    # §4-shaped payload (nested actor) keeps decision_outcome as a top-level scalar.
    v4 = DecisionEvent(**{
        "id": "dec-2", "tool": "nexus", "action_type": "committee_gate",
        "summary": "s", "source_created_at": "2026-06-09T00:00:00Z",
        "decision_outcome": "pass",
        "actor": {"user_id": "u1", "org_id": "acme", "role": "ceo"},
    })
    assert v4.decision_outcome == "pass"


def test_decision_outcome_defaults_empty_when_absent():
    """Backward compatibility: omitting decision_outcome is valid, defaults ''."""
    event = DecisionEvent(
        id="dec-3", tool="mapforge", action_type="data_contribution",
        occurred_at="2026-06-09T00:00:00Z", summary="s",
    )
    assert event.decision_outcome == ""


def test_decision_outcome_persisted_in_merge(client, demo_api_key, mock_kg):
    """The MERGE that writes the :Decision node must SET decision_outcome and
    pass it as a parameter — otherwise the field is dropped at the store."""
    payload = {
        "id": "dec-42", "tool": "nexus", "action_type": "hypothesis_verdict",
        "summary": "F. prausnitzii depletion refutes the protective hypothesis",
        "decision_outcome": "refuted",
        "occurred_at": "2026-06-09T00:00:00Z",
        "entity_tags": [],  # keep to a single execute_cypher call (the MERGE)
    }
    with patch("api.routes.provenance_decisions.get_kg", return_value=mock_kg):
        mock_kg.execute_cypher = MagicMock(side_effect=[None])
        r = client.post(
            "/api/v1/provenance/decisions", json=payload,
            headers={"X-API-Key": demo_api_key},
        )
    assert r.status_code == 201, r.text
    merge_query, merge_params = mock_kg.execute_cypher.call_args_list[0].args
    assert "d.decision_outcome = $decision_outcome" in merge_query
    assert merge_params["decision_outcome"] == "refuted"


def test_cited_decision_surfaces_decision_outcome():
    """The query-result model exposes decision_outcome so the verdict reaches
    the caller, not just the graph."""
    cited = CitedDecision(
        id="dec-1", action_type="hypothesis_verdict", decision_outcome="refuted",
        summary="s", rationale="r", tool="nexus",
        occurred_at="2026-06-09T00:00:00Z", actor_user="u1", actor_org="acme",
        role="scientist", about=["crohns disease"], citations=[],
    )
    assert cited.decision_outcome == "refuted"


def test_ledger_entry_surfaces_decision_outcome():
    """LedgerEntry carries decision_outcome (default '' for :Contribution rows)."""
    entry = LedgerEntry(
        source="nexus", kind="hypothesis_verdict", decision_outcome="pass",
        summary="s", occurred_at="2026-06-09T00:00:00Z",
    )
    assert entry.decision_outcome == "pass"
    contrib = LedgerEntry(
        source="mapforge", kind="data_contribution",
        summary="disbiome", occurred_at="2026-06-09T00:00:00Z",
    )
    assert contrib.decision_outcome == ""


# --- Fix 2: normalize_disease_name in :ABOUT linking ----------------------

def test_about_linking_normalizes_disease_tags(client, demo_api_key, mock_kg):
    """Disease entity_tags must be matched with normalize_disease_name, not a
    bare toLower. We assert the ingest passes the normalized form so
    "Crohn's Disease" can match the graph's "crohns disease" key."""
    raw_tag = "Crohn's Disease"
    expected_norm = normalize_disease_name(raw_tag)
    assert expected_norm == "crohns disease"  # guard: normalize != toLower
    assert expected_norm != raw_tag.lower()

    payload = {
        "id": "dec-7", "tool": "nexus", "action_type": "hypothesis_verdict",
        "summary": "s", "occurred_at": "2026-06-09T00:00:00Z",
        "entity_tags": [raw_tag, "NCBITaxon:853"],
    }
    with patch("api.routes.provenance_decisions.get_kg", return_value=mock_kg):
        mock_kg.execute_cypher = MagicMock(side_effect=[
            None,  # MERGE
            [{"tag": raw_tag, "matched": True},
             {"tag": "NCBITaxon:853", "matched": True}],  # ABOUT
        ])
        r = client.post(
            "/api/v1/provenance/decisions", json=payload,
            headers={"X-API-Key": demo_api_key},
        )
    assert r.status_code == 201, r.text

    about_query, about_params = mock_kg.execute_cypher.call_args_list[1].args
    # Disease matches on the normalized key; Taxon matches verbatim on taxon_id.
    pairs = about_params["tag_pairs"]
    by_raw = {p["raw"]: p["norm"] for p in pairs}
    assert by_raw[raw_tag] == "crohns disease"
    assert by_raw["NCBITaxon:853"] == normalize_disease_name("NCBITaxon:853")
    assert "e.name_normalized = tp.norm" in about_query
    assert "e.taxon_id = tp.raw" in about_query
    assert "toLower(tag)" not in about_query  # the old, broken predicate is gone


# --- Fix 3: context_snapshot + source_ref.native_id persistence (#212) -----

def test_context_snapshot_and_native_id_are_model_fields():
    """The model accepts the spec's `context_snapshot` (nested) and pulls
    `native_id` out of a nested `source_ref`, instead of silently dropping both."""
    v4 = DecisionEvent(**{
        "id": "dec-cs-1", "tool": "workbench", "action_type": "pipeline_run",
        "summary": "s", "source_created_at": "2026-06-11T00:00:00Z",
        "actor": {"user_id": "u1", "org_id": "acme", "role": "service"},
        "source_ref": {"tool": "workbench", "native_id": "run-abc"},
        "context_snapshot": {"verdict": "pass", "caveats": ["low n"]},
    })
    assert v4.source_native_id == "run-abc"
    assert v4.context_snapshot == {"verdict": "pass", "caveats": ["low n"]}


def test_context_snapshot_defaults_absent():
    """Backward compatibility: omitting both is valid — None / '' defaults."""
    event = DecisionEvent(
        id="dec-cs-2", tool="mapforge", action_type="data_contribution",
        occurred_at="2026-06-11T00:00:00Z", summary="s",
    )
    assert event.context_snapshot is None
    assert event.source_native_id == ""


def test_context_snapshot_persisted_as_json_string_in_merge(client, demo_api_key, mock_kg):
    """The MERGE must SET context_snapshot (JSON-serialized — Neo4j has no map
    property type) and source_native_id, so neither is dropped at the store."""
    payload = {
        "id": "dec-cs-3", "tool": "workbench", "action_type": "pipeline_run",
        "summary": "ran workflow", "occurred_at": "2026-06-11T00:00:00Z",
        "source_ref": {"tool": "workbench", "native_id": "run-xyz"},
        "context_snapshot": {"verdict": "pass", "n": 2},
        "entity_tags": [],  # keep to a single execute_cypher call (the MERGE)
    }
    with patch("api.routes.provenance_decisions.get_kg", return_value=mock_kg):
        mock_kg.execute_cypher = MagicMock(side_effect=[None])
        r = client.post(
            "/api/v1/provenance/decisions", json=payload,
            headers={"X-API-Key": demo_api_key},
        )
    assert r.status_code == 201, r.text
    merge_query, merge_params = mock_kg.execute_cypher.call_args_list[0].args
    assert "d.context_snapshot = $context_snapshot" in merge_query
    assert "d.source_native_id = $source_native_id" in merge_query
    # Stored as a JSON string (not a dict — Neo4j would reject a map property).
    assert merge_params["context_snapshot"] == json.dumps(
        {"verdict": "pass", "n": 2}, sort_keys=True
    )
    assert isinstance(merge_params["context_snapshot"], str)
    assert merge_params["source_native_id"] == "run-xyz"


def test_context_snapshot_none_passed_as_null(client, demo_api_key, mock_kg):
    """When absent, context_snapshot is passed as None (Cypher null), not the
    string 'null' — so the property is simply unset."""
    payload = {
        "id": "dec-cs-4", "tool": "mapforge", "action_type": "data_contribution",
        "summary": "s", "occurred_at": "2026-06-11T00:00:00Z", "entity_tags": [],
    }
    with patch("api.routes.provenance_decisions.get_kg", return_value=mock_kg):
        mock_kg.execute_cypher = MagicMock(side_effect=[None])
        r = client.post(
            "/api/v1/provenance/decisions", json=payload,
            headers={"X-API-Key": demo_api_key},
        )
    assert r.status_code == 201, r.text
    _, merge_params = mock_kg.execute_cypher.call_args_list[0].args
    assert merge_params["context_snapshot"] is None
