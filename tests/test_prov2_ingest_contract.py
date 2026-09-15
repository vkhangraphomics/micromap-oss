"""
Test PROV-2: DecisionEvent tolerant ingest with §4 payload normalization.

Validates that the endpoint accepts the Nexus §4 shape and normalizes it
to the internal flat contract before ingestion.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.routes.provenance_decisions import DecisionEvent


@pytest.fixture
def client():
    """FastAPI TestClient for the MicroMap API."""
    return TestClient(app)


@pytest.fixture
def demo_api_key():
    """Demo API key for testing."""
    return "demo-key-123"


@pytest.fixture
def mock_kg():
    """Mock MicrobiomeKG instance to avoid requiring a live Neo4j."""
    kg = MagicMock()
    kg.execute_cypher = MagicMock(return_value=[])
    return kg


def test_v4_payload_normalizes_and_validates(demo_api_key):
    """Test that the §4 payload shape normalizes without 422 validation error."""
    payload = {
        "id": "dec-42",
        "tool": "nexus",
        "action_type": "hypothesis_verdict",
        "summary": "F. prausnitzii depletion supports the Crohn hypothesis",
        "rationale": "Evidence from microbiome study",
        "actor": {"user_id": "u1", "org_id": "acme", "role": "scientist"},
        "entity_tags": [
            {"key": "NCBITaxon:853", "label": "Taxon"},
            {"key": "Crohn disease", "label": "Disease"},
        ],
        "evidence_refs": [{"uri": "https://disbiome.example.com"}],
        "source_created_at": "2026-06-09T00:00:00Z",
        "source_ref": {"kind": "investigation", "investigation_id": "inv-1", "tool": "nexus"},
    }

    # Validate at the pydantic model level first
    event = DecisionEvent(**payload)

    # Verify normalization happened
    assert event.occurred_at == "2026-06-09T00:00:00Z"
    assert event.actor_user == "u1"
    assert event.actor_org == "acme"
    assert event.role == "scientist"
    assert event.entity_tags == ["NCBITaxon:853", "Crohn disease"]
    assert event.evidence_refs == ["https://disbiome.example.com"]
    assert event.tool == "nexus"


def test_v4_payload_with_bearer_token(client, demo_api_key, mock_kg):
    """Test that the endpoint accepts Bearer token authentication."""
    payload = {
        "id": "dec-42",
        "tool": "nexus",
        "action_type": "hypothesis_verdict",
        "summary": "Test summary",
        "rationale": "Test rationale",
        "actor": {"user_id": "u1", "org_id": "acme", "role": "scientist"},
        "entity_tags": [{"key": "NCBITaxon:853", "label": "Taxon"}],
        "evidence_refs": [{"uri": "https://example.com"}],
        "source_created_at": "2026-06-09T00:00:00Z",
        "source_ref": {"tool": "nexus"},
    }

    with patch("api.routes.provenance_decisions.get_kg", return_value=mock_kg):
        # Mock execute_cypher to return decision merged and ABOUT query results
        mock_kg.execute_cypher = MagicMock(
            side_effect=[
                None,  # First call: MERGE decision (no return)
                [{"tag": "NCBITaxon:853", "matched": False}],  # Second call: ABOUT query
            ]
        )

        # Use Bearer token instead of X-API-Key
        r = client.post(
            "/api/v1/provenance/decisions",
            json=payload,
            headers={"Authorization": "Bearer demo-key-123"},
        )

        # Should not return 422 (validation error)
        assert r.status_code != 422, f"Got 422: {r.text}"
        # Should return 201 (created) or 401 (auth) but not validation error
        assert r.status_code in [201, 401], f"Unexpected status: {r.status_code}, body: {r.text}"


def test_v4_entity_tags_flattening():
    """Test entity_tags extraction from list of dicts."""
    payload = {
        "id": "dec-1",
        "tool": "nexus",
        "action_type": "hypothesis_verdict",
        "summary": "s",
        "occurred_at": "2026-06-09T00:00:00Z",
        "entity_tags": [
            {"key": "NCBITaxon:853", "label": "Taxon"},
            {"key": "Crohn disease", "label": "Disease"},
            {"key": "", "label": "Empty"},  # Should be filtered out
        ],
    }

    event = DecisionEvent(**payload)
    assert event.entity_tags == ["NCBITaxon:853", "Crohn disease"]


def test_v4_evidence_refs_extraction():
    """Test evidence_refs extraction from list of dicts."""
    payload = {
        "id": "dec-1",
        "tool": "nexus",
        "action_type": "hypothesis_verdict",
        "summary": "s",
        "occurred_at": "2026-06-09T00:00:00Z",
        "evidence_refs": [
            {"uri": "https://disbiome.example.com"},
            {"url": "https://pubmed.ncbi.nlm.nih.gov/12345"},
            {"ref": "https://fallback.example.com"},
            {"uri": None, "url": "https://secondary.example.com"},  # fallback to url
            {},  # Should be filtered out (no ref)
        ],
    }

    event = DecisionEvent(**payload)
    assert event.evidence_refs == [
        "https://disbiome.example.com",
        "https://pubmed.ncbi.nlm.nih.gov/12345",
        "https://fallback.example.com",
        "https://secondary.example.com",
    ]


def test_v4_actor_flattening():
    """Test actor dict flattening to flat fields."""
    payload = {
        "id": "dec-1",
        "tool": "nexus",
        "action_type": "hypothesis_verdict",
        "summary": "s",
        "occurred_at": "2026-06-09T00:00:00Z",
        "actor": {"user_id": "alice", "org_id": "acme", "role": "scientist"},
    }

    event = DecisionEvent(**payload)
    assert event.actor_user == "alice"
    assert event.actor_org == "acme"
    assert event.role == "scientist"


def test_v4_source_created_at_fallback():
    """Test occurred_at fallback from source_created_at."""
    payload = {
        "id": "dec-1",
        "tool": "nexus",
        "action_type": "hypothesis_verdict",
        "summary": "s",
        "source_created_at": "2026-06-09T10:30:00Z",
        # No occurred_at provided
    }

    event = DecisionEvent(**payload)
    assert event.occurred_at == "2026-06-09T10:30:00Z"


def test_v4_tool_fallback_from_source_ref():
    """Test tool fallback from source_ref.tool."""
    payload = {
        "id": "dec-1",
        "action_type": "hypothesis_verdict",
        "summary": "s",
        "occurred_at": "2026-06-09T00:00:00Z",
        "source_ref": {"tool": "nexus", "investigation_id": "inv-1"},
        # No tool provided at top level
    }

    event = DecisionEvent(**payload)
    assert event.tool == "nexus"


def test_v4_backward_compat_flat_fields():
    """Test that flat fields still work (backward compatibility)."""
    payload = {
        "id": "dec-1",
        "tool": "mapforge",
        "action_type": "data_contribution",
        "occurred_at": "2026-06-09T00:00:00Z",
        "summary": "s",
        "rationale": "r",
        "actor_user": "bob",
        "actor_org": "graphomics",
        "role": "service",
        "entity_tags": ["NCBITaxon:100", "Disease X"],
        "evidence_refs": ["https://example.com"],
    }

    event = DecisionEvent(**payload)
    assert event.tool == "mapforge"
    assert event.actor_user == "bob"
    assert event.actor_org == "graphomics"
    assert event.entity_tags == ["NCBITaxon:100", "Disease X"]


def test_v4_empty_lists():
    """Test that empty lists are preserved and don't cause validation errors."""
    payload = {
        "id": "dec-1",
        "tool": "nexus",
        "action_type": "hypothesis_verdict",
        "occurred_at": "2026-06-09T00:00:00Z",
        "summary": "s",
        "entity_tags": [],
        "evidence_refs": [],
    }

    event = DecisionEvent(**payload)
    assert event.entity_tags == []
    assert event.evidence_refs == []


def test_endpoint_with_mocked_kg(client, demo_api_key, mock_kg):
    """Integration-like test: POST to endpoint with mocked KG."""
    payload = {
        "id": "dec-42",
        "tool": "nexus",
        "action_type": "hypothesis_verdict",
        "summary": "F. prausnitzii depletion supports Crohn",
        "rationale": "Observed in cohort study",
        "actor": {"user_id": "u1", "org_id": "acme", "role": "scientist"},
        "entity_tags": [
            {"key": "NCBITaxon:853", "label": "Taxon"},
            {"key": "Crohn disease", "label": "Disease"},
        ],
        "evidence_refs": [{"uri": "https://disbiome.example.com/study"}],
        "source_created_at": "2026-06-09T00:00:00Z",
        "source_ref": {"tool": "nexus"},
    }

    with patch("api.routes.provenance_decisions.get_kg", return_value=mock_kg):
        # Mock both MERGE (returns nothing) and ABOUT query
        mock_kg.execute_cypher = MagicMock(
            side_effect=[
                None,  # MERGE decision
                [
                    {"tag": "NCBITaxon:853", "matched": True},
                    {"tag": "Crohn disease", "matched": True},
                ],  # ABOUT query
            ]
        )

        r = client.post(
            "/api/v1/provenance/decisions",
            json=payload,
            headers={"X-API-Key": demo_api_key},
        )

        # Status should be 201 (created)
        assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"

        body = r.json()
        assert body["id"] == "dec-42"
        assert body["entity_tags_matched"] == 2
        assert body["entity_tags_unmatched"] == []


def test_endpoint_with_partial_matches(client, demo_api_key, mock_kg):
    """Test endpoint when only some entity_tags match in the graph."""
    payload = {
        "id": "dec-99",
        "tool": "nexus",
        "action_type": "hypothesis_verdict",
        "summary": "Test partial match",
        "actor": {"user_id": "u1", "org_id": "acme", "role": "scientist"},
        "entity_tags": [
            {"key": "NCBITaxon:999999", "label": "Taxon"},  # Won't match
            {"key": "Crohn disease", "label": "Disease"},  # Will match
        ],
        "source_created_at": "2026-06-09T00:00:00Z",
    }

    with patch("api.routes.provenance_decisions.get_kg", return_value=mock_kg):
        mock_kg.execute_cypher = MagicMock(
            side_effect=[
                None,  # MERGE decision
                [
                    {"tag": "NCBITaxon:999999", "matched": False},
                    {"tag": "Crohn disease", "matched": True},
                ],
            ]
        )

        r = client.post(
            "/api/v1/provenance/decisions",
            json=payload,
            headers={"X-API-Key": demo_api_key},
        )

        assert r.status_code == 201
        body = r.json()
        assert body["entity_tags_matched"] == 1
        assert body["entity_tags_unmatched"] == ["NCBITaxon:999999"]
