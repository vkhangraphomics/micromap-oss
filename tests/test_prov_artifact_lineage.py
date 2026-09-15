"""#310: :Artifact nodes + PRODUCED/USED/DERIVED_FROM cross-run lineage.

The provenance schema had no way to express cross-run lineage — evidence_refs
was an opaque node property, not traversable. #321 made ingest retain each
evidence entry's content hash (checksum) and role; this materializes those as
:Artifact nodes keyed on the hash, links the decision that PRODUCED/USED each,
and connects an output to its inputs via DERIVED_FROM so run N+1's output
traces back through run N.
"""
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app
from database.load_knowledge_graph import UNIQUENESS_CONSTRAINTS

client = TestClient(app)
KEY = {"X-API-Key": "demo-key-123"}
HASH_IN = "sha256:" + "1" * 64
HASH_OUT = "sha256:" + "2" * 64


@pytest.fixture
def mock_kg():
    kg = MagicMock()
    kg.execute_cypher = MagicMock(return_value=[])
    return kg


def _base(**over):
    p = {"id": "dec-art-1", "tool": "workbench", "action_type": "pipeline_run",
         "summary": "s", "occurred_at": "2026-08-26T00:00:00Z"}
    p.update(over)
    return p


def _calls(mock_kg):
    return mock_kg.execute_cypher.call_args_list


def _find_call(mock_kg, needle):
    for c in _calls(mock_kg):
        if needle in c.args[0]:
            return c
    return None


# --- schema constraint ----------------------------------------------------

def test_artifact_uniqueness_constraint_is_registered():
    joined = " ".join(UNIQUENESS_CONSTRAINTS)
    assert "(n:Artifact)" in joined
    assert "(n.organization_id, n.sha256) IS UNIQUE" in joined


# --- ingest: :Artifact + PRODUCED / USED ----------------------------------

def test_ingest_creates_artifacts_from_checksummed_evidence(mock_kg):
    payload = _base(evidence_refs=[
        {"uri": "mongo://in.fastq", "checksum": HASH_IN, "role": "INPUT"},
        {"uri": "mongo://out.bam", "checksum": HASH_OUT, "role": "OUTPUT"},
    ])
    with patch("api.routes.provenance_decisions.get_kg", return_value=mock_kg):
        mock_kg.execute_cypher = MagicMock(side_effect=[
            None,                       # MERGE decision
            [{"artifacts": 2}],         # MERGE :Artifact
            [{"derived": 1}],           # DERIVED_FROM
        ])
        r = client.post("/api/v1/provenance/decisions", json=payload, headers=KEY)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["artifacts_written"] == 2
    assert body["derived_from_created"] == 1

    art_call = _find_call(mock_kg, "MERGE (a:Artifact")
    assert art_call is not None, "no :Artifact MERGE issued"
    arts = art_call.args[1]["artifacts"]
    assert {a["checksum"] for a in arts} == {HASH_IN, HASH_OUT}
    assert {a["role"] for a in arts} == {"INPUT", "OUTPUT"}


def test_artifact_merge_is_keyed_on_org_and_hash(mock_kg):
    payload = _base(evidence_refs=[{"uri": "u", "checksum": HASH_IN, "role": "INPUT"}])
    with patch("api.routes.provenance_decisions.get_kg", return_value=mock_kg):
        mock_kg.execute_cypher = MagicMock(side_effect=[None, [{"artifacts": 1}], [{"derived": 0}]])
        client.post("/api/v1/provenance/decisions", json=payload, headers=KEY)
    q = _find_call(mock_kg, "MERGE (a:Artifact").args[0]
    assert "MERGE (a:Artifact {organization_id: $org, sha256: art.checksum})" in q
    assert "(d)-[:USED]->(a)" in q
    assert "(d)-[:PRODUCED]->(a)" in q


def test_derived_from_links_outputs_to_inputs(mock_kg):
    payload = _base(evidence_refs=[
        {"uri": "i", "checksum": HASH_IN, "role": "INPUT"},
        {"uri": "o", "checksum": HASH_OUT, "role": "OUTPUT"},
    ])
    with patch("api.routes.provenance_decisions.get_kg", return_value=mock_kg):
        mock_kg.execute_cypher = MagicMock(side_effect=[None, [{"artifacts": 2}], [{"derived": 1}]])
        client.post("/api/v1/provenance/decisions", json=payload, headers=KEY)
    q = _find_call(mock_kg, "DERIVED_FROM").args[0]
    assert "-[:PRODUCED]->(out:Artifact)" in q
    assert "-[:USED]->(inp:Artifact)" in q
    assert "MERGE (out)-[r:DERIVED_FROM]->(inp)" in q


def test_bare_string_evidence_creates_no_artifact(mock_kg):
    payload = _base(evidence_refs=["https://bare.example.com"])
    with patch("api.routes.provenance_decisions.get_kg", return_value=mock_kg):
        mock_kg.execute_cypher = MagicMock(side_effect=[None])  # MERGE decision only
        r = client.post("/api/v1/provenance/decisions", json=payload, headers=KEY)
    assert r.status_code == 201, r.text
    assert _find_call(mock_kg, "MERGE (a:Artifact") is None
    assert r.json()["artifacts_written"] == 0


def test_result_fields_default_to_zero_without_evidence(mock_kg):
    with patch("api.routes.provenance_decisions.get_kg", return_value=mock_kg):
        mock_kg.execute_cypher = MagicMock(side_effect=[None])
        r = client.post("/api/v1/provenance/decisions", json=_base(), headers=KEY)
    body = r.json()
    assert body["artifacts_written"] == 0 and body["derived_from_created"] == 0


# --- lineage endpoint -----------------------------------------------------

def test_artifact_lineage_returns_ancestors(mock_kg):
    rows = [
        {"base_sha256": HASH_OUT, "sha256": HASH_IN, "uri": "mongo://in.fastq",
         "depth": 1, "produced_by": {"id": "dec-prev", "action_type": "pipeline_run"}},
    ]
    with patch("api.routes.provenance_artifacts.get_kg", return_value=mock_kg):
        mock_kg.execute_cypher = MagicMock(return_value=rows)
        r = client.post("/api/v1/provenance/artifact-lineage",
                        json={"sha256": HASH_OUT}, headers=KEY)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["found"] is True
    assert body["count"] == 1
    assert body["ancestors"][0]["sha256"] == HASH_IN
    assert body["ancestors"][0]["depth"] == 1
    assert body["ancestors"][0]["produced_by"]["id"] == "dec-prev"


def test_artifact_lineage_found_true_no_ancestors(mock_kg):
    # base artifact exists but has no DERIVED_FROM ancestors → one row, anc null
    rows = [{"base_sha256": HASH_IN, "sha256": None, "uri": None,
             "depth": None, "produced_by": None}]
    with patch("api.routes.provenance_artifacts.get_kg", return_value=mock_kg):
        mock_kg.execute_cypher = MagicMock(return_value=rows)
        r = client.post("/api/v1/provenance/artifact-lineage",
                        json={"sha256": HASH_IN}, headers=KEY)
    body = r.json()
    assert body["found"] is True
    assert body["ancestors"] == []


def test_artifact_lineage_not_found(mock_kg):
    with patch("api.routes.provenance_artifacts.get_kg", return_value=mock_kg):
        mock_kg.execute_cypher = MagicMock(return_value=[])
        r = client.post("/api/v1/provenance/artifact-lineage",
                        json={"sha256": "sha256:" + "9" * 64}, headers=KEY)
    body = r.json()
    assert body["found"] is False and body["ancestors"] == []


def test_artifact_lineage_query_is_org_scoped(mock_kg):
    with patch("api.routes.provenance_artifacts.get_kg", return_value=mock_kg):
        mock_kg.execute_cypher = MagicMock(return_value=[])
        client.post("/api/v1/provenance/artifact-lineage",
                    json={"sha256": HASH_IN}, headers=KEY)
    q = mock_kg.execute_cypher.call_args.args[0]
    assert "a.organization_id = $organization_id" in q
    assert "DERIVED_FROM" in q
