"""The digest, chain and verifier are pure and deterministic, so a counterparty can
reimplement them and get the same answer."""
import re

from micromap_format import chain


def _ev(i: int) -> dict:
    return {"id": f"dec-{i}", "tool": "example", "action_type": "target_rationale",
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
