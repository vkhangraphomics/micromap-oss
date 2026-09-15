"""#325: the chain export/head REST endpoints + the standalone verifier CLI.

The endpoints are thin wrappers over the (Postgres-tested) export function; here we
prove org-scoping, the JSONL/head shapes, and the disabled-log 503 without a DB by
substituting the export. The CLI is exercised directly on a temp JSONL file.
"""
import json

from fastapi.testclient import TestClient

from api import decision_log
from api.dependencies import resolve_organization_id, verify_api_key
from api.main import app
from api.provenance import chain, verify_chain
from api.provenance.chain import chain_entries

client = TestClient(app)

_ENTRIES = chain_entries([{"id": f"dec-{i}", "summary": f"d{i}"} for i in range(1, 4)])


def _auth_as(org: str):
    app.dependency_overrides[resolve_organization_id] = lambda: org
    app.dependency_overrides[verify_api_key] = lambda: "test-key"


def _clear_auth():
    app.dependency_overrides.pop(resolve_organization_id, None)
    app.dependency_overrides.pop(verify_api_key, None)


# --- route -----------------------------------------------------------------

def test_export_streams_the_callers_whole_chain_as_jsonl(monkeypatch):
    seen = {}

    def fake_export(org, **_):
        seen["org"] = org
        yield from _ENTRIES

    monkeypatch.setattr(decision_log, "is_enabled", lambda: True)
    monkeypatch.setattr(chain, "export_org_chain", fake_export)
    _auth_as("org1")
    try:
        resp = client.get("/api/v1/provenance/chain/export")
    finally:
        _clear_auth()

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(ln) for ln in resp.text.splitlines() if ln.strip()]
    assert [e["seq"] for e in lines] == [1, 2, 3]
    assert seen["org"] == "org1"                      # exports the CALLER's org, whole
    # The streamed chain verifies and its head is the last line's digest.
    result = verify_chain.verify_chain(lines)
    assert result.ok and result.head == lines[-1]["digest"]


def test_head_returns_the_anchor(monkeypatch):
    monkeypatch.setattr(decision_log, "is_enabled", lambda: True)
    monkeypatch.setattr(chain, "export_org_chain", lambda org, **_: iter(_ENTRIES))
    _auth_as("org1")
    try:
        resp = client.get("/api/v1/provenance/chain/head")
    finally:
        _clear_auth()
    assert resp.status_code == 200
    assert resp.json() == {"head": _ENTRIES[-1]["digest"], "count": 3}


def test_export_503_when_log_disabled(monkeypatch):
    monkeypatch.setattr(decision_log, "is_enabled", lambda: False)
    _auth_as("org1")
    try:
        resp = client.get("/api/v1/provenance/chain/export")
    finally:
        _clear_auth()
    assert resp.status_code == 503
    assert "disabled" in resp.json()["detail"].lower()


# --- verifier CLI ----------------------------------------------------------

def _write_jsonl(path, entries):
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    return str(path)


def test_cli_verifies_a_good_chain(tmp_path, capsys):
    f = _write_jsonl(tmp_path / "chain.jsonl", _ENTRIES)
    rc = verify_chain.main([f])
    assert rc == 0
    assert "OK:" in capsys.readouterr().out


def test_cli_expect_head_flags_a_mismatch(tmp_path):
    f = _write_jsonl(tmp_path / "chain.jsonl", _ENTRIES)
    assert verify_chain.main([f, "--expect-head", "sha256:deadbeef"]) == 1


def test_cli_detects_a_tampered_chain(tmp_path):
    tampered = [dict(_ENTRIES[0]), {**_ENTRIES[1], "event": {"id": "swapped"}}, dict(_ENTRIES[2])]
    f = _write_jsonl(tmp_path / "bad.jsonl", tampered)
    assert verify_chain.main([f]) == 1
