"""#323: the ledger read API must be walkable and honest about truncation.

Every provenance read endpoint was `ORDER BY occurred_at DESC LIMIT $cap` with
no cursor and a `count` that meant rows RETURNED, not rows MATCHING — so the
newest N events were the only events retrievable, and a caller summing a page
believed it had the whole set. This blocks a billing-period statement and the
verifiable-log export (#325).

Contract under test:
- keyset pagination over the COMPOSITE (occurred_at, id) — a bare occurred_at
  cursor silently drops rows sharing a timestamp, and a batch drain lands many
  in the same instant;
- `has_more` distinguishes a full page from the end;
- `next_cursor` is opaque and round-trips;
- a malformed cursor is rejected, not silently ignored;
- caps stay — the fix is a continuation token, not a bigger ceiling.
"""
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from api.main import app
from api.routes import provenance_decisions as pd

client = TestClient(app)
KEY = {"X-API-Key": "demo-key-123"}
TS = "2026-08-02T00:00:00Z"


def _dec_row(i, ts=TS):
    return {
        "id": f"dec-{i}", "action_type": "pipeline_run", "decision_outcome": "",
        "summary": f"s{i}", "rationale": "", "tool": "nexus", "occurred_at": ts,
        "actor_user": "", "actor_org": "", "role": "", "about": [], "citations": [],
    }


# --- cursor codec + keyset predicate -------------------------------------

def test_cursor_roundtrips():
    c = pd._encode_cursor(TS, "dec-9")
    assert pd._decode_cursor(c) == (TS, "dec-9")


def test_decode_none_or_empty_is_none():
    assert pd._decode_cursor(None) is None
    assert pd._decode_cursor("") is None


def test_keyset_predicate_carries_the_id_tiebreaker():
    # The whole point of #323: a bare occurred_at cursor drops timestamp
    # collisions. The predicate must compare id when occurred_at ties.
    p = pd._keyset_predicate("d.")
    assert "d.occurred_at < $cursor_ts" in p
    assert "d.occurred_at = $cursor_ts AND d.id < $cursor_id" in p


# --- /provenance/query ----------------------------------------------------

def test_query_full_page_reports_has_more_and_next_cursor():
    kg = MagicMock()
    kg.execute_cypher = MagicMock(return_value=[_dec_row(i) for i in range(3)])
    with patch.object(pd, "get_kg", return_value=kg):
        r = client.post("/api/v1/provenance/query",
                        json={"entity": "x", "limit": 2}, headers=KEY)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["decisions"]) == 2            # trimmed to the cap
    assert body["has_more"] is True
    assert pd._decode_cursor(body["next_cursor"]) == (TS, "dec-1")  # last kept row
    _, params = kg.execute_cypher.call_args.args
    assert params["limit_plus1"] == 3             # over-fetched by one


def test_query_last_page_has_no_more_and_no_cursor():
    kg = MagicMock()
    kg.execute_cypher = MagicMock(return_value=[_dec_row(0)])
    with patch.object(pd, "get_kg", return_value=kg):
        r = client.post("/api/v1/provenance/query",
                        json={"entity": "x", "limit": 50}, headers=KEY)
    body = r.json()
    assert body["has_more"] is False
    assert body["next_cursor"] is None
    assert len(body["decisions"]) == 1


def test_query_with_cursor_binds_both_ts_and_id():
    kg = MagicMock()
    kg.execute_cypher = MagicMock(return_value=[])
    cur = pd._encode_cursor(TS, "dec-5")
    with patch.object(pd, "get_kg", return_value=kg):
        r = client.post("/api/v1/provenance/query",
                        json={"entity": "x", "cursor": cur}, headers=KEY)
    assert r.status_code == 200
    q, params = kg.execute_cypher.call_args.args
    assert params["cursor_ts"] == TS
    assert params["cursor_id"] == "dec-5"
    assert "$cursor_id" in q                        # composite keyset wired in


def test_invalid_cursor_is_rejected_not_ignored():
    kg = MagicMock()
    kg.execute_cypher = MagicMock(return_value=[])
    with patch.object(pd, "get_kg", return_value=kg):
        r = client.post("/api/v1/provenance/query",
                        json={"entity": "x", "cursor": "!!!not-valid!!!"}, headers=KEY)
    assert r.status_code == 422


def test_query_cap_still_enforced():
    # The fix is a continuation token, not a bigger ceiling.
    with patch.object(pd, "get_kg", return_value=MagicMock()):
        r = client.post("/api/v1/provenance/query",
                        json={"entity": "x", "limit": 5000}, headers=KEY)
    assert r.status_code == 422   # > 500 cap


# --- /provenance/current + /provenance/as-of ------------------------------

def test_current_paginates():
    kg = MagicMock()
    kg.execute_cypher = MagicMock(return_value=[_dec_row(i) for i in range(3)])
    with patch.object(pd, "get_kg", return_value=kg):
        r = client.post("/api/v1/provenance/current",
                        json={"entity": "x", "limit": 2}, headers=KEY)
    body = r.json()
    assert body["has_more"] is True
    assert pd._decode_cursor(body["next_cursor"]) == (TS, "dec-1")


def test_as_of_paginates_and_keeps_cursor():
    kg = MagicMock()
    kg.execute_cypher = MagicMock(return_value=[_dec_row(i) for i in range(3)])
    cur = pd._encode_cursor(TS, "dec-0")
    with patch.object(pd, "get_kg", return_value=kg):
        r = client.post("/api/v1/provenance/as-of",
                        json={"entity": "x", "as_of": "2026-08-02", "limit": 2,
                              "cursor": cur}, headers=KEY)
    assert r.status_code == 200, r.text
    _, params = kg.execute_cypher.call_args.args
    assert params["cursor_ts"] == TS and params["cursor_id"] == "dec-0"
    assert r.json()["has_more"] is True


# --- /provenance/ledger (heterogeneous UNION) -----------------------------

def test_ledger_paginates_with_synthesized_id():
    kg = MagicMock()
    rows = [{"source": "nexus", "kind": "pipeline_run", "decision_outcome": "",
             "summary": f"s{i}", "occurred_at": TS, "id": f"dec-{i}"} for i in range(3)]
    kg.execute_cypher = MagicMock(return_value=rows)
    with patch.object(pd, "get_kg", return_value=kg):
        r = client.post("/api/v1/provenance/ledger",
                        json={"entity": "x", "limit": 2}, headers=KEY)
    body = r.json()
    assert len(body["entries"]) == 2
    assert body["has_more"] is True
    assert pd._decode_cursor(body["next_cursor"]) == (TS, "dec-1")


# --- /provenance/search (relevance-ranked: has_more, no keyset walk) -------

def test_search_reports_has_more_but_offers_no_cursor():
    kg = MagicMock()
    kg.execute_cypher = MagicMock(
        return_value=[{**_dec_row(i), "score": 0.9 - i * 0.1} for i in range(3)])
    embedder = MagicMock()
    embedder.embed_query.return_value = [0.1, 0.2, 0.3, 0.4]
    with patch.object(pd, "get_kg", return_value=kg), \
         patch("api.embeddings.get_embedder", return_value=embedder):
        r = client.post("/api/v1/provenance/search",
                        json={"query": "x", "limit": 2}, headers=KEY)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["decisions"]) == 2
    assert body["has_more"] is True
    # relevance ranking is not a walkable enumeration — no cursor offered
    assert body.get("next_cursor") is None
