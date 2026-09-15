"""#325: chained, counterparty-verifiable digest over the decision log.

The core (digest + chain + verifier) is pure and deterministic — no DB — so a
counterparty can reimplement it and get the same answer. Integration tests below
export a real Postgres log (testcontainers) and prove the export is verifiable,
per-org, walks every row (#323), and that deletion is detectable via the head.
"""
import re

import pytest

from api import decision_log
from api.provenance import chain


def _ev(i: int) -> dict:
    return {"id": f"dec-{i}", "tool": "nexus", "action_type": "target_rationale",
            "summary": f"decision {i}", "occurred_at": f"2026-06-11T17:0{i}:00Z"}


# --- pure: digest + chain --------------------------------------------------

def test_entry_digest_is_deterministic_prefixed_and_content_bound():
    d1 = chain.entry_digest(None, _ev(1))
    assert d1 == chain.entry_digest(None, _ev(1))          # deterministic
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", d1)        # prefixed hex
    assert chain.entry_digest(None, _ev(2)) != d1          # event-bound
    assert chain.entry_digest("sha256:00", _ev(1)) != d1   # prev-bound


def test_chain_links_each_entry_to_its_predecessor():
    entries = chain.chain_entries([_ev(1), _ev(2), _ev(3)])
    assert [e["seq"] for e in entries] == [1, 2, 3]
    assert entries[0]["prev"] is None
    assert entries[1]["prev"] == entries[0]["digest"]      # commits to predecessor
    assert entries[2]["prev"] == entries[1]["digest"]
    # each digest is exactly entry_digest(prev, event)
    for e in entries:
        assert e["digest"] == chain.entry_digest(e["prev"], e["event"])


# --- pure: verifier --------------------------------------------------------

def test_verify_accepts_a_wellformed_chain_and_reports_the_head():
    entries = chain.chain_entries([_ev(i) for i in range(1, 6)])
    result = chain.verify_chain(entries)
    assert result.ok is True
    assert result.count == 5
    assert result.head == entries[-1]["digest"]
    assert result.first_bad_seq is None


def test_verify_empty_chain_is_ok_with_null_head():
    result = chain.verify_chain([])
    assert result.ok is True and result.head is None and result.count == 0


def test_verify_detects_a_deleted_middle_entry():
    entries = chain.chain_entries([_ev(1), _ev(2), _ev(3)])
    tampered = [entries[0], entries[2]]                    # drop seq 2
    result = chain.verify_chain(tampered)
    assert result.ok is False
    assert result.first_bad_seq == 3                       # seq 3's prev no longer links
    assert "prev" in result.reason.lower() or "link" in result.reason.lower()


def test_verify_detects_a_modified_event():
    entries = chain.chain_entries([_ev(1), _ev(2), _ev(3)])
    entries[1] = {**entries[1], "event": _ev(99)}          # edit the event, keep the digest
    result = chain.verify_chain(entries)
    assert result.ok is False
    assert result.first_bad_seq == 2
    assert "digest" in result.reason.lower()


def test_verify_detects_a_truncated_tail_only_via_head_comparison():
    """Dropping the LAST entry still yields a valid-looking chain — the only tell
    is the head. This is why a counterparty must compare the head, not just replay."""
    entries = chain.chain_entries([_ev(1), _ev(2), _ev(3)])
    truncated = entries[:2]
    result = chain.verify_chain(truncated)
    assert result.ok is True                               # internally consistent
    assert result.head == entries[1]["digest"]             # but head != full-log head
    assert result.head != chain.verify_chain(entries).head


# --- integration: export from a real Postgres log --------------------------

def _dec(i: int, tool: str = "nexus") -> dict:
    """A DecisionEvent payload as decision_log.append expects it."""
    return {"id": f"dec-{i}", "tool": tool, "action_type": "target_rationale",
            "decision_outcome": "supported", "occurred_at": f"2026-06-11T17:00:0{i%10}Z",
            "summary": f"decision {i}", "rationale": "because", "evidence_refs": [],
            "entity_tags": [], "supersedes": []}


@pytest.fixture(scope="module")
def pg_dsn():
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers[postgres] not installed")
    try:
        with PostgresContainer("postgres:16") as pg:
            dsn = re.sub(r"\+\w+", "", pg.get_connection_url(), count=1)
            decision_log.init_schema(dsn)
            yield dsn
    except Exception as e:  # pragma: no cover - env-dependent
        pytest.skip(f"Could not start Postgres container: {e}")


def test_export_walks_the_whole_org_log_and_verifies(pg_dsn):
    for i in range(1, 6):
        assert decision_log.append(_dec(i), "acme", dsn=pg_dsn) is True
    entries = list(chain.export_org_chain("acme", dsn=pg_dsn))
    assert len(entries) == 5
    assert [e["seq"] for e in entries] == [1, 2, 3, 4, 5]
    result = chain.verify_chain(entries)
    assert result.ok is True and result.count == 5
    assert result.head == entries[-1]["digest"]


def test_export_is_per_org_and_whole_not_a_filtered_extract(pg_dsn):
    decision_log.append(_dec(101, "nexus"), "orgA", dsn=pg_dsn)
    decision_log.append(_dec(102, "nexus"), "orgA", dsn=pg_dsn)
    decision_log.append(_dec(201, "nexus"), "orgB", dsn=pg_dsn)
    a = list(chain.export_org_chain("orgA", dsn=pg_dsn))
    b = list(chain.export_org_chain("orgB", dsn=pg_dsn))
    assert {e["event"]["id"] for e in a} == {"dec-101", "dec-102"}   # only orgA, all of it
    assert {e["event"]["id"] for e in b} == {"dec-201"}
    assert chain.verify_chain(a).head != chain.verify_chain(b).head  # independent chains


def test_export_walks_beyond_a_single_batch(pg_dsn):
    for i in range(300, 425):                                        # 125 rows
        decision_log.append(_dec(i), "bulk", dsn=pg_dsn)
    entries = list(chain.export_org_chain("bulk", dsn=pg_dsn, batch_size=50))
    assert len(entries) == 125                                       # not truncated at 50 (#323)
    assert chain.verify_chain(entries).ok is True


def test_deleting_a_row_changes_the_head_even_bypassing_the_trigger(pg_dsn):
    """The append-only trigger blocks DELETE — but a counterparty can't trust that.
    Simulate a malicious bypass (drop the trigger, delete a row): a fresh export's
    head no longer matches the head recorded before, which is the tamper signal."""
    import psycopg

    for i in range(500, 505):
        decision_log.append(_dec(i), "tamper", dsn=pg_dsn)
    head_before = chain.verify_chain(list(chain.export_org_chain("tamper", dsn=pg_dsn))).head

    with psycopg.connect(pg_dsn) as conn:
        conn.execute("ALTER TABLE decision_events DISABLE TRIGGER decision_events_immutable")
        conn.execute("DELETE FROM decision_events WHERE id = 'dec-502'")
        conn.execute("ALTER TABLE decision_events ENABLE TRIGGER decision_events_immutable")
        conn.commit()

    head_after = chain.verify_chain(list(chain.export_org_chain("tamper", dsn=pg_dsn))).head
    assert head_after != head_before                                 # deletion is detectable
