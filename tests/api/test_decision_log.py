"""Append-only Postgres decision-event log (#190 pillar 2).

Unit tests for the content-addressed key + the disabled-when-unconfigured path,
plus integration tests against a real Postgres (testcontainers): insert,
idempotent ON CONFLICT, the append-only trigger, and that the full event
(JSONB fields + payload) round-trips.
"""
import re

import pytest

from api import decision_log


def _event(event_id: str = "dec-1", tool: str = "nexus") -> dict:
    return {
        "id": event_id, "tool": tool, "action_type": "target_rationale",
        "decision_outcome": "supported", "occurred_at": "2026-06-11T17:00:00Z",
        "summary": "why we pursue X", "rationale": "because Y",
        "evidence_refs": ["http://evidence/1"], "actor_user": "ceo",
        "actor_org": "acme", "role": "ceo", "entity_tags": ["parkinson disease"],
        "supersedes": [], "context_snapshot": {"verdict": "supported"},
        "source_native_id": "native-1",
    }


# --- unit ------------------------------------------------------------------

def test_idempotency_key_is_deterministic_and_scoped():
    k = decision_log.idempotency_key("nexus", "dec-1")
    assert k == decision_log.idempotency_key("nexus", "dec-1")
    assert len(k) == 64
    assert k != decision_log.idempotency_key("nexus", "dec-2")
    assert k != decision_log.idempotency_key("workbench", "dec-1")


def test_disabled_when_no_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert decision_log.is_enabled() is False
    # append + init_schema are clean no-ops (return False / do nothing, no raise)
    assert decision_log.append(_event(), "acme") is False
    decision_log.init_schema()


# --- integration -----------------------------------------------------------

@pytest.fixture(scope="module")
def pg_dsn():
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers[postgres] not installed")
    try:
        with PostgresContainer("postgres:16") as pg:
            # psycopg needs a bare 'postgresql://' DSN, not 'postgresql+driver://'.
            dsn = re.sub(r"\+\w+", "", pg.get_connection_url(), count=1)
            decision_log.init_schema(dsn)
            yield dsn
    except Exception as e:  # pragma: no cover - env-dependent
        pytest.skip(f"Could not start Postgres container: {e}")


def test_append_inserts_full_event_and_is_idempotent(pg_dsn):
    import psycopg

    assert decision_log.append(_event("dec-A"), "acme", dsn=pg_dsn) is True
    # Same (tool, id) again -> ON CONFLICT DO NOTHING -> not inserted.
    assert decision_log.append(_event("dec-A"), "acme", dsn=pg_dsn) is False

    with psycopg.connect(pg_dsn) as conn:
        row = conn.execute(
            "SELECT organization_id, tool, decision_outcome, evidence_refs, "
            "context, payload FROM decision_events WHERE id = 'dec-A'"
        ).fetchone()
    assert row[0] == "acme"
    assert row[1] == "nexus"
    assert row[2] == "supported"
    assert row[3] == ["http://evidence/1"]        # JSONB -> list
    assert row[4] == {"verdict": "supported"}     # context JSONB
    assert row[5]["summary"] == "why we pursue X"  # full payload preserved


def test_log_is_append_only(pg_dsn):
    import psycopg

    decision_log.append(_event("dec-B"), "acme", dsn=pg_dsn)
    with psycopg.connect(pg_dsn) as conn, pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE decision_events SET summary = 'tampered' WHERE id = 'dec-B'")
    with psycopg.connect(pg_dsn) as conn, pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM decision_events WHERE id = 'dec-B'")


def test_org_is_stored_per_event(pg_dsn):
    import psycopg

    decision_log.append(_event("dec-C"), "acme", dsn=pg_dsn)
    decision_log.append(_event("dec-D"), "globex", dsn=pg_dsn)
    with psycopg.connect(pg_dsn) as conn:
        acme = conn.execute(
            "SELECT count(*) FROM decision_events WHERE organization_id = 'acme'"
        ).fetchone()[0]
        globex = conn.execute(
            "SELECT count(*) FROM decision_events WHERE organization_id = 'globex'"
        ).fetchone()[0]
    assert acme >= 1 and globex >= 1


# --- route wiring (ingest_decision) ----------------------------------------

def _ingest(event_id: str, organization_id: str = "acme"):
    """Call ingest_decision directly with a mocked Neo4j KG; return (result, fake_kg)."""
    import asyncio
    from unittest.mock import MagicMock, patch

    from api.routes import provenance_decisions as pd

    fake_kg = MagicMock()
    fake_kg.execute_cypher = MagicMock(return_value=[])
    event = pd.DecisionEvent(**_event(event_id))
    with patch.object(pd, "get_kg", return_value=fake_kg):
        result = asyncio.run(pd.ingest_decision(event, organization_id=organization_id))
    return result, fake_kg


def test_ingest_appends_to_log_when_configured(pg_dsn, monkeypatch):
    import psycopg

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    result, fake_kg = _ingest("dec-route-1")
    assert result.id == "dec-route-1"
    assert fake_kg.execute_cypher.called  # Neo4j projection still ran
    with psycopg.connect(pg_dsn) as conn:
        n = conn.execute(
            "SELECT count(*) FROM decision_events WHERE id = 'dec-route-1'"
        ).fetchone()[0]
    assert n == 1


def test_ingest_skips_log_when_unconfigured(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    result, fake_kg = _ingest("dec-route-2")
    assert result.id == "dec-route-2"
    assert fake_kg.execute_cypher.called  # Neo4j-only path unaffected


def test_ingest_returns_503_when_log_write_fails(monkeypatch):
    from fastapi import HTTPException

    # Unreachable DSN -> append() raises psycopg.Error -> durable 503.
    monkeypatch.setenv("DATABASE_URL", "postgresql://nope:nope@127.0.0.1:1/none")
    with pytest.raises(HTTPException) as ei:
        _ingest("dec-route-3")
    assert ei.value.status_code == 503

