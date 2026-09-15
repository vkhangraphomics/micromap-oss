"""Append-only Postgres event log for DecisionEvents (#190 pillar 2).

The append-only **system of record** for decision provenance: every
``DecisionEvent`` is appended here, idempotent on a content-addressed key,
org-scoped, immutable (an UPDATE/DELETE trigger raises). The Neo4j ``:Decision``
graph is a rebuildable projection over this log.

Configured via ``DATABASE_URL``. When unset the log is **disabled** and callers
skip it, preserving Neo4j-only deploys until Postgres is provisioned. When it IS
configured, ``append`` raises on a real DB error so an event is never silently
lost (durable semantics — see #190).
"""
from __future__ import annotations

import hashlib
import os
from typing import Any, Mapping, Optional

import psycopg
from psycopg.types.json import Json


def database_url() -> Optional[str]:
    """The configured Postgres DSN, or None when the log is disabled."""
    return os.environ.get("DATABASE_URL") or None


def is_enabled() -> bool:
    return database_url() is not None


def idempotency_key(tool: str, event_id: str) -> str:
    """Content-addressed dedup key, per the canonical spec §4:
    ``sha256(tool | 'decision_log' | id)``. The append uses this for ON CONFLICT,
    so an at-least-once producer retrying the same event never double-writes."""
    return hashlib.sha256(f"{tool}|decision_log|{event_id}".encode()).hexdigest()


# Statements are run individually (psycopg's extended protocol rejects
# multi-statement strings). All are idempotent — safe to run on every startup.
_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS decision_events (
        pk               BIGSERIAL PRIMARY KEY,
        id               TEXT        NOT NULL,
        idempotency_key  TEXT        NOT NULL UNIQUE,
        organization_id  TEXT        NOT NULL,
        tool             TEXT        NOT NULL,
        action_type      TEXT        NOT NULL,
        decision_outcome TEXT        NOT NULL DEFAULT '',
        occurred_at      TIMESTAMPTZ,
        summary          TEXT        NOT NULL DEFAULT '',
        rationale        TEXT        NOT NULL DEFAULT '',
        actor_user       TEXT        NOT NULL DEFAULT '',
        actor_org        TEXT        NOT NULL DEFAULT '',
        role             TEXT        NOT NULL DEFAULT '',
        source_native_id TEXT        NOT NULL DEFAULT '',
        evidence_refs    JSONB       NOT NULL DEFAULT '[]'::jsonb,
        entity_tags      JSONB       NOT NULL DEFAULT '[]'::jsonb,
        supersedes       JSONB       NOT NULL DEFAULT '[]'::jsonb,
        context          JSONB,
        payload          JSONB       NOT NULL,
        inserted_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS decision_events_org_time_idx "
    "ON decision_events (organization_id, occurred_at)",
    """
    CREATE OR REPLACE FUNCTION decision_events_no_mutate() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'decision_events is append-only (% blocked)', TG_OP;
    END;
    $$ LANGUAGE plpgsql
    """,
    "DROP TRIGGER IF EXISTS decision_events_immutable ON decision_events",
    "CREATE TRIGGER decision_events_immutable "
    "BEFORE UPDATE OR DELETE ON decision_events "
    "FOR EACH ROW EXECUTE FUNCTION decision_events_no_mutate()",
)


_INSERT_SQL = """
    INSERT INTO decision_events
        (id, idempotency_key, organization_id, tool, action_type, decision_outcome,
         occurred_at, summary, rationale, actor_user, actor_org, role,
         source_native_id, evidence_refs, entity_tags, supersedes, context, payload)
    VALUES
        (%(id)s, %(key)s, %(org)s, %(tool)s, %(action_type)s, %(decision_outcome)s,
         %(occurred_at)s, %(summary)s, %(rationale)s, %(actor_user)s, %(actor_org)s,
         %(role)s, %(source_native_id)s, %(evidence_refs)s, %(entity_tags)s,
         %(supersedes)s, %(context)s, %(payload)s)
    ON CONFLICT (idempotency_key) DO NOTHING
    RETURNING id
"""


def init_schema(dsn: Optional[str] = None) -> None:
    """Create the table, index, and append-only trigger (idempotent). No-op when
    the log is disabled (no DSN)."""
    dsn = dsn or database_url()
    if not dsn:
        return
    with psycopg.connect(dsn) as conn:
        for stmt in _SCHEMA_STATEMENTS:
            conn.execute(stmt)
        conn.commit()


def append(payload: Mapping[str, Any], organization_id: str, *, dsn: Optional[str] = None) -> bool:
    """Append one DecisionEvent to the log.

    ``payload`` is a ``DecisionEvent.model_dump()`` dict. Returns True if a row
    was inserted, False if it was already present (idempotent ON CONFLICT) or the
    log is disabled. Raises ``psycopg.Error`` on a real DB failure — callers that
    treat the log as the system of record must NOT swallow it.
    """
    dsn = dsn or database_url()
    if not dsn:
        return False
    tool = payload["tool"]
    event_id = payload["id"]
    context = payload.get("context_snapshot")
    params = {
        "id": event_id,
        "key": idempotency_key(tool, event_id),
        "org": organization_id,
        "tool": tool,
        "action_type": payload.get("action_type", ""),
        "decision_outcome": payload.get("decision_outcome", ""),
        "occurred_at": payload.get("occurred_at"),
        "summary": payload.get("summary", ""),
        "rationale": payload.get("rationale", ""),
        "actor_user": payload.get("actor_user", ""),
        "actor_org": payload.get("actor_org", ""),
        "role": payload.get("role", ""),
        "source_native_id": payload.get("source_native_id", ""),
        "evidence_refs": Json(payload.get("evidence_refs", [])),
        "entity_tags": Json(payload.get("entity_tags", [])),
        "supersedes": Json(payload.get("supersedes", [])),
        "context": Json(context) if context is not None else None,
        "payload": Json(dict(payload)),
    }
    with psycopg.connect(dsn) as conn:
        row = conn.execute(_INSERT_SQL, params).fetchone()
        conn.commit()
    return row is not None
