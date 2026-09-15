"""#268: guarded MCP write surface — admin-only, dry-run, provenance-by-default.

An agent can read and append over MCP but could not *correct* the graph
(query_graph is read-only, MapForge is additive). These tools add a small,
bounded write surface: set a property (reversible flag) or delete a node/edge,
identified by a typed selector (never raw Cypher), gated to admin, refused when
identity is unconfigured, dry-run by default, capped, and provenance-recorded.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from micromap_mcp.auth import Principal
from micromap_mcp.tools import write as write_mod
from micromap_mcp.tools.write import register_write_tools, MAX_AFFECTED, build_match


# --- async fakes (mirror test_tools_cypher's, plus execute_write) ----------

class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    async def data(self):
        return self._rows


class FakeTx:
    def __init__(self, rows_by_substring=None, default_rows=None):
        self.rows_by_substring = rows_by_substring or {}
        self.default_rows = [] if default_rows is None else default_rows
        self.queries = []

    async def run(self, query, **kwargs):
        self.queries.append((query, kwargs))
        for sub, rows in self.rows_by_substring.items():
            if sub in query:
                return FakeResult(rows)
        return FakeResult(self.default_rows)


class FakeSession:
    def __init__(self, tx):
        self._tx = tx

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute_read(self, txfunc, *a, **k):
        return await txfunc(self._tx, *a, **k)

    async def execute_write(self, txfunc, *a, **k):
        return await txfunc(self._tx, *a, **k)


class FakeDriver:
    def __init__(self, tx):
        self._tx = tx
        self.closed = False

    def session(self, database=None):
        return FakeSession(self._tx)

    async def close(self):
        self.closed = True


@pytest.fixture
def patch_driver(monkeypatch):
    def _install(tx):
        driver = FakeDriver(tx)
        monkeypatch.setattr(write_mod, "AsyncGraphDatabase",
                            SimpleNamespace(driver=lambda *a, **k: driver))
        return driver
    return _install


@pytest.fixture
def tools():
    return register_write_tools(
        app=None, neo4j_uri="bolt://x:7687", neo4j_user="neo4j",
        neo4j_password="pw", database="micromap", return_callables=True)


def _as(principal):
    return patch("micromap_mcp.tools.write.current_principal", return_value=principal)


NODE_TARGET = {"kind": "node", "label": "Taxon", "key": "taxon_id",
               "value": "NCBITaxon:999"}
REL_TARGET = {"kind": "relationship", "type": "ASSOCIATED_WITH_DISEASE",
              "from": {"label": "Taxon", "key": "taxon_id", "value": "NCBITaxon:1"},
              "to": {"label": "Disease", "key": "name_normalized", "value": "parkinson disease"}}


# --- the auth gate --------------------------------------------------------

async def test_non_admin_is_refused(tools, patch_driver, monkeypatch):
    monkeypatch.setenv("MCP_TOKEN_ORGS", "tok:acme:service")  # identity configured
    patch_driver(FakeTx())
    for role in ("service", "user", "member", ""):
        with _as(Principal(org_id="acme", role=role)):
            out = await tools["graph_delete"](target=REL_TARGET)
        assert "error" in out, role
        assert "admin" in out["error"].lower()


async def test_admin_is_allowed(tools, patch_driver, monkeypatch):
    monkeypatch.setenv("MCP_TOKEN_ORGS", "tok:graphomics:admin")
    patch_driver(FakeTx(rows_by_substring={"count(*) AS affected": [{"affected": 3, "sample": []}]}))
    with _as(Principal(org_id="graphomics", role="admin")):
        out = await tools["graph_delete"](target=REL_TARGET)
    assert "error" not in out, out
    assert out["dry_run"] is True and out["affected"] == 3


async def test_refused_when_identity_not_configured(tools, patch_driver, monkeypatch):
    # fail-CLOSED: unlike the read gate, a write surface must not be open during
    # the single-shared-token migration window.
    monkeypatch.delenv("MCP_TOKEN_ORGS", raising=False)
    for k in ("JWT_SECRET", "JWT_PUBLIC_KEY", "JWKS_URL"):
        monkeypatch.delenv(k, raising=False)
    tx = FakeTx()
    patch_driver(tx)
    with _as(Principal(org_id="default", role="admin")):
        out = await tools["graph_delete"](target=REL_TARGET)
    assert "error" in out and "not configured" in out["error"].lower()
    assert tx.queries == [], "must not touch the DB when refused"


# --- dry-run / confirm / cap ----------------------------------------------

async def test_delete_dry_run_is_default_and_mutates_nothing(tools, patch_driver, monkeypatch):
    monkeypatch.setenv("MCP_TOKEN_ORGS", "tok:g:admin")
    tx = FakeTx(rows_by_substring={"count(*) AS affected": [{"affected": 2, "sample": ["a", "b"]}]})
    patch_driver(tx)
    with _as(Principal(org_id="g", role="admin")):
        out = await tools["graph_delete"](target=REL_TARGET)
    assert out["dry_run"] is True and out["affected"] == 2
    assert not any("DELETE" in q for q, _ in tx.queries)


async def test_delete_without_confirm_is_refused_with_preview(tools, patch_driver, monkeypatch):
    monkeypatch.setenv("MCP_TOKEN_ORGS", "tok:g:admin")
    tx = FakeTx(rows_by_substring={"count(*) AS affected": [{"affected": 2, "sample": []}]})
    patch_driver(tx)
    with _as(Principal(org_id="g", role="admin")):
        out = await tools["graph_delete"](target=REL_TARGET, dry_run=False)
    assert "error" in out and "confirm" in out["error"].lower()
    assert out["affected"] == 2
    assert not any("DELETE" in q for q, _ in tx.queries)


async def test_delete_confirmed_executes_and_records_provenance(tools, patch_driver, monkeypatch):
    monkeypatch.setenv("MCP_TOKEN_ORGS", "tok:g:admin")
    tx = FakeTx(rows_by_substring={
        "count(*) AS affected": [{"affected": 1, "sample": []}],
        "DELETE": [{"deleted": 1}],
    })
    patch_driver(tx)
    with _as(Principal(org_id="g", role="admin", user_id="alice")):
        out = await tools["graph_delete"](target=REL_TARGET, dry_run=False, confirm=True)
    assert out["dry_run"] is False and out["deleted"] == 1
    assert out["provenance_decision_id"]
    assert any("MERGE (d:Decision" in q for q, _ in tx.queries), "no provenance recorded"
    prov_q = next(q for q, _ in tx.queries if "MERGE (d:Decision" in q)
    assert "graph_correction" in prov_q


async def test_delete_over_cap_is_refused(tools, patch_driver, monkeypatch):
    monkeypatch.setenv("MCP_TOKEN_ORGS", "tok:g:admin")
    tx = FakeTx(rows_by_substring={"count(*) AS affected": [{"affected": MAX_AFFECTED + 1, "sample": []}]})
    patch_driver(tx)
    with _as(Principal(org_id="g", role="admin")):
        out = await tools["graph_delete"](target=REL_TARGET, dry_run=False, confirm=True)
    assert "error" in out and str(MAX_AFFECTED) in out["error"]
    assert not any("DELETE" in q for q, _ in tx.queries)


async def test_zero_matches_is_a_noop(tools, patch_driver, monkeypatch):
    monkeypatch.setenv("MCP_TOKEN_ORGS", "tok:g:admin")
    tx = FakeTx(rows_by_substring={"count(*) AS affected": [{"affected": 0, "sample": []}]})
    patch_driver(tx)
    with _as(Principal(org_id="g", role="admin")):
        out = await tools["graph_delete"](target=NODE_TARGET, dry_run=False, confirm=True)
    assert out["affected"] == 0
    assert not any("DETACH DELETE" in q for q, _ in tx.queries)


# --- set_property (reversible; no confirm needed) -------------------------

async def test_set_property_applies_and_records_provenance(tools, patch_driver, monkeypatch):
    monkeypatch.setenv("MCP_TOKEN_ORGS", "tok:g:admin")
    tx = FakeTx(rows_by_substring={
        "count(*) AS affected": [{"affected": 803, "sample": []}],
    })
    patch_driver(tx)
    with _as(Principal(org_id="g", role="admin")):
        # 803 > cap, so even set must respect the cap
        out = await tools["graph_set_property"](
            target=REL_TARGET, prop_key="flagged_unsupported", prop_value=True,
            dry_run=False)
    assert "error" in out and str(MAX_AFFECTED) in out["error"]


async def test_set_property_within_cap_writes_and_provenances(tools, patch_driver, monkeypatch):
    monkeypatch.setenv("MCP_TOKEN_ORGS", "tok:g:admin")
    tx = FakeTx(rows_by_substring={"count(*) AS affected": [{"affected": 1, "sample": []}]})
    patch_driver(tx)
    with _as(Principal(org_id="g", role="admin")):
        out = await tools["graph_set_property"](
            target=NODE_TARGET, prop_key="curated", prop_value="reviewed", dry_run=False)
    assert out["dry_run"] is False and out["updated"] == 1
    assert out["provenance_decision_id"]
    assert any("setProperty" in q or "SET" in q for q, _ in tx.queries)


# --- selector -> bounded MATCH (no injection) ------------------------------

def test_build_match_node_binds_everything_as_params():
    match, params = build_match(NODE_TARGET)
    # label, key, value all bound — no interpolation of caller strings
    assert "$label IN labels(n)" in match
    assert "n[$key] = $value" in match
    assert params["label"] == "Taxon" and params["key"] == "taxon_id"
    assert params["value"] == "NCBITaxon:999"
    assert "Taxon" not in match  # the label VALUE is not in the query text


def test_build_match_relationship_uses_type_predicate_not_literal():
    match, params = build_match(REL_TARGET)
    assert "type(r) = $type" in match
    assert "ASSOCIATED_WITH_DISEASE" not in match  # bound, not interpolated
    assert params["type"] == "ASSOCIATED_WITH_DISEASE"
    assert params["from_value"] == "NCBITaxon:1"
    assert params["to_value"] == "parkinson disease"


def test_build_match_rejects_unknown_kind():
    with pytest.raises(ValueError):
        build_match({"kind": "everything"})
