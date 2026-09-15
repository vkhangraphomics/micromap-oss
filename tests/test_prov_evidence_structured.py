"""#321: ingest must retain `checksum` and `role` from evidence_refs.

Workbench#688 ships per-evidence-ref content identity (`checksum`) and
consumed-vs-produced (`role`); the old `_accept_v4` flattened every entry to a
bare URI string and discarded both — silently dropping the producer side of
cross-run lineage (#310 / #301 depend on these).

Contract under test:
- structured `{uri, checksum, role}` round-trips through the model AND is
  persisted to the graph and the Postgres payload;
- bare-string and mixed payloads still ingest unchanged (widening, not
  tightening);
- checksum shape is validated (`sha256:` + 64 hex, case-normalized); malformed
  is ignored, not stored; a missing checksum stays absent, never an empty string.
"""
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.routes.provenance_decisions import DecisionEvent

_HASH = "sha256:" + "a" * 64
_HASH2 = "sha256:" + "b" * 64


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock_kg():
    kg = MagicMock()
    kg.execute_cypher = MagicMock(return_value=[])
    return kg


def _base(**over):
    p = {
        "id": "dec-ev-1",
        "tool": "workbench",
        "action_type": "pipeline_run",
        "summary": "s",
        "occurred_at": "2026-08-02T00:00:00Z",
    }
    p.update(over)
    return p


# --- model-level normalization -------------------------------------------

def test_structured_evidence_ref_round_trips():
    event = DecisionEvent(**_base(evidence_refs=[
        {"uri": "mongo://bucket/in.fastq", "checksum": _HASH, "role": "INPUT"},
    ]))
    assert event.evidence_refs == ["mongo://bucket/in.fastq"]  # flat still works
    assert len(event.evidence) == 1
    assert event.evidence[0].uri == "mongo://bucket/in.fastq"
    assert event.evidence[0].checksum == _HASH
    assert event.evidence[0].role == "INPUT"


def test_bare_string_evidence_still_works():
    event = DecisionEvent(**_base(evidence_refs=["https://disbiome.example.com"]))
    assert event.evidence_refs == ["https://disbiome.example.com"]
    assert len(event.evidence) == 1
    assert event.evidence[0].uri == "https://disbiome.example.com"
    assert event.evidence[0].checksum is None
    assert event.evidence[0].role is None


def test_mixed_bare_and_structured():
    event = DecisionEvent(**_base(evidence_refs=[
        "https://bare.example.com",
        {"uri": "mongo://o.bam", "checksum": _HASH2, "role": "OUTPUT"},
    ]))
    assert event.evidence_refs == ["https://bare.example.com", "mongo://o.bam"]
    assert event.evidence[0].checksum is None
    assert event.evidence[1].checksum == _HASH2
    assert event.evidence[1].role == "OUTPUT"


def test_malformed_checksum_is_ignored_not_stored():
    event = DecisionEvent(**_base(evidence_refs=[
        {"uri": "mongo://x", "checksum": "not-a-real-hash", "role": "INPUT"},
    ]))
    assert event.evidence[0].checksum is None      # ignored, not stored as garbage
    assert event.evidence[0].role == "INPUT"       # role still kept
    assert event.evidence_refs == ["mongo://x"]    # decision still ingests


def test_missing_checksum_is_none_never_empty_string():
    event = DecisionEvent(**_base(evidence_refs=[{"uri": "mongo://x", "role": "OUTPUT"}]))
    assert event.evidence[0].checksum is None
    assert event.evidence[0].checksum != ""


def test_checksum_hex_case_is_normalized_to_lowercase():
    event = DecisionEvent(**_base(evidence_refs=[
        {"uri": "mongo://x", "checksum": "sha256:" + "A" * 64},
    ]))
    assert event.evidence[0].checksum == "sha256:" + "a" * 64


def test_entry_without_resolvable_uri_is_dropped():
    event = DecisionEvent(**_base(evidence_refs=[
        {},                                   # no uri/url/ref → dropped
        {"checksum": _HASH},                  # no uri → dropped
        {"url": "https://u.example.com"},     # url fallback kept
    ]))
    assert event.evidence_refs == ["https://u.example.com"]
    assert len(event.evidence) == 1


# --- persistence: graph MERGE + Postgres payload -------------------------

def _merge_params(mock_kg):
    """The params dict of the first execute_cypher call (the :Decision MERGE)."""
    return mock_kg.execute_cypher.call_args_list[0].args[1]


def test_ingest_persists_structured_evidence_to_graph(client, mock_kg):
    payload = _base(id="dec-ev-graph", evidence_refs=[
        {"uri": "mongo://in", "checksum": _HASH, "role": "INPUT"},
    ])
    with patch("api.routes.provenance_decisions.get_kg", return_value=mock_kg):
        # calls: MERGE decision, then the #310 :Artifact MERGE + DERIVED_FROM
        # (this payload carries a checksum, so the artifact step runs).
        mock_kg.execute_cypher = MagicMock(
            side_effect=[None, [{"artifacts": 1}], [{"derived": 0}]])
        r = client.post("/api/v1/provenance/decisions", json=payload,
                        headers={"X-API-Key": "demo-key-123"})
    assert r.status_code == 201, r.text
    params = _merge_params(mock_kg)
    assert "evidence" in params and params["evidence"] is not None
    stored = json.loads(params["evidence"])
    assert stored[0]["checksum"] == _HASH
    assert stored[0]["role"] == "INPUT"


def test_ingest_bare_payload_sets_no_evidence_property(client, mock_kg):
    payload = _base(id="dec-ev-bare", evidence_refs=["https://bare.example.com"])
    with patch("api.routes.provenance_decisions.get_kg", return_value=mock_kg):
        mock_kg.execute_cypher = MagicMock(side_effect=[None, []])
        r = client.post("/api/v1/provenance/decisions", json=payload,
                        headers={"X-API-Key": "demo-key-123"})
    assert r.status_code == 201, r.text
    # No structured content → d.evidence stays null (legacy decisions stay clean).
    assert _merge_params(mock_kg)["evidence"] is None


def test_model_dump_payload_carries_checksum_and_role():
    """decision_log.append persists model_dump() as the Postgres payload; the
    structured evidence (with checksum/role) must be in it."""
    event = DecisionEvent(**_base(evidence_refs=[
        {"uri": "mongo://in", "checksum": _HASH, "role": "INPUT"},
    ]))
    dumped = event.model_dump()
    assert dumped["evidence"][0]["checksum"] == _HASH
    assert dumped["evidence"][0]["role"] == "INPUT"
