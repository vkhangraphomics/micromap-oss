"""#325: chained, counterparty-verifiable digest over the append-only decision log.

The Postgres ``decision_events`` log is append-only (a trigger blocks UPDATE/DELETE),
but that immutability is *our* database enforcing a rule — precisely the assurance a
counterparty cannot check. This turns the log into an independently-verifiable
artifact: a per-org export is a standalone JSONL stream where each entry commits to
its predecessor via a SHA-256 chain, plus a ``head`` digest the counterparty records
and re-compares against a fresh export. Delete or reorder any row and the recomputed
head diverges — detectable without trusting us.

The digest is deliberately trivial to reimplement (that is the whole point):

    canonical = json.dumps(event, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest    = "sha256:" + sha256((prev + "\\n" + canonical).encode("utf-8")).hexdigest()

where ``prev`` is the predecessor entry's digest string, or ``""`` for the first
entry (exported as ``prev: null``), and ``event`` is the full DecisionEvent payload
exactly as it appears in the export. The chain is computed at export time from the
immutable, ``pk``-ordered rows — no digest is stored, so nothing about the write path
or existing rows changes (#325 decision: compute-at-export).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Mapping, Optional

# Postgres jsonb does not preserve key order; canonical JSON (sorted keys, compact
# separators, UTF-8) makes the digest independent of storage/serialization order so
# our compute and a counterparty's agree byte-for-byte.
_CANON = dict(sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def canonical_event(event: Mapping[str, Any]) -> str:
    """The canonical JSON string of an event — the exact bytes fed to the digest."""
    return json.dumps(event, **_CANON)


def entry_digest(prev: Optional[str], event: Mapping[str, Any]) -> str:
    """``sha256:<hex>`` over the predecessor digest and the canonical event.
    ``prev=None`` (the genesis entry) hashes an empty predecessor string."""
    material = (prev or "") + "\n" + canonical_event(event)
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def chain_entries(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Build the chain over ``events`` (already in log/``pk`` order). Each output is
    ``{seq, prev, digest, event}`` with ``seq`` a 1-based per-chain position."""
    out: list[dict[str, Any]] = []
    prev: Optional[str] = None
    for i, event in enumerate(events, start=1):
        digest = entry_digest(prev, event)
        out.append({"seq": i, "prev": prev, "digest": digest, "event": dict(event)})
        prev = digest
    return out


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    head: Optional[str]        # the chain head (last digest), or None for an empty chain
    count: int
    first_bad_seq: Optional[int] = None
    reason: str = ""


def verify_chain(entries: Iterable[Mapping[str, Any]]) -> VerifyResult:
    """Replay a chain, recomputing every digest, and report the first break.

    Self-contained: a counterparty runs this over the exported JSONL, with no access
    to our database, and gets the same ``head``. Catches a modified event (digest no
    longer matches its content) and a deleted/reordered row (an entry's ``prev`` no
    longer matches the running head). A truncated *tail* still verifies internally —
    the tell is that ``head`` differs from the head the counterparty recorded, which
    is why the head must be compared, not just the replay accepted.
    """
    prev: Optional[str] = None
    count = 0
    for entry in entries:
        count += 1
        seq = entry.get("seq", count)
        if entry.get("prev") != prev:
            return VerifyResult(False, prev, count, seq,
                                f"entry {seq}: prev does not link to the running head "
                                f"(expected {prev!r}, found {entry.get('prev')!r}) — "
                                f"a row was deleted or reordered")
        recomputed = entry_digest(prev, entry.get("event", {}))
        if entry.get("digest") != recomputed:
            return VerifyResult(False, prev, count, seq,
                                f"entry {seq}: digest does not match its event "
                                f"(recomputed {recomputed}, found {entry.get('digest')}) "
                                f"— the event was modified")
        prev = recomputed
    return VerifyResult(True, prev, count, None, "")


# --- export from the Postgres log (integration) ----------------------------

# Walk the org's WHOLE log in pk order, streaming in batches so a large log does
# not materialize at once. #323 (pagination) closed the read-truncation gap; here
# we keyset on pk directly since the export is server-side and org-scoped.
_EXPORT_SQL = """
    SELECT pk, payload FROM decision_events
    WHERE organization_id = %(org)s AND pk > %(after)s
    ORDER BY pk ASC
    LIMIT %(batch)s
"""


def _iter_org_payloads(organization_id: str, dsn: str, batch_size: int) -> Iterator[dict]:
    import psycopg

    after = 0
    with psycopg.connect(dsn) as conn:
        while True:
            rows = conn.execute(
                _EXPORT_SQL,
                {"org": organization_id, "after": after, "batch": batch_size},
            ).fetchall()
            if not rows:
                return
            for pk, payload in rows:
                after = pk
                yield payload


def export_org_chain(
    organization_id: str, *, dsn: Optional[str] = None, batch_size: int = 1000,
) -> Iterator[dict[str, Any]]:
    """Stream the per-org chain entries ``{seq, prev, digest, event}`` from the log.

    Per-org and *whole* (never a filtered extract — that would return the
    counterparty to trusting us). ``dsn=None`` reads ``DATABASE_URL``.
    """
    from api import decision_log

    dsn = dsn or decision_log.database_url()
    if not dsn:
        raise RuntimeError(
            "decision log is disabled (no DATABASE_URL) — nothing to export"
        )
    prev: Optional[str] = None
    for seq, payload in enumerate(_iter_org_payloads(organization_id, dsn, batch_size), start=1):
        digest = entry_digest(prev, payload)
        yield {"seq": seq, "prev": prev, "digest": digest, "event": payload}
        prev = digest
