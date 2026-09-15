"""Regression tests for the generic Cypher tools (GPV-400).

The bug: with the **async** Neo4j driver, the transaction function
``lambda tx: tx.run(q).data()`` calls ``.data()`` on the *coroutine* returned
by ``tx.run`` (it is not awaited), raising
``'coroutine' object has no attribute 'data'`` on every call. These fakes
reproduce the async driver contract (``tx.run`` and ``result.data`` are both
awaitables, ``execute_read`` awaits the transaction function), so the tests
below fail against the pre-fix code and pass once both awaits are in place.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from micromap_mcp.auth import Principal
from micromap_mcp.tools import cypher as cypher_mod
from micromap_mcp.tools.cypher import register_cypher_tools, _fetch_data, _counts_query


def test_counts_query_aggregates_before_naming_so_empty_labels_survive():
    """A zero-node label must still produce a row. `RETURN $name, count(n)`
    makes `name` a grouping key, so an empty label yields zero groups → zero
    rows and the tombstone silently vanishes — the exact bug #320 fixes. The
    builder must aggregate first in a bare `WITH count(...)`. Verified live:
    MATCH (n:`Metabolite`) WITH count(n) AS count RETURN 'Metabolite', count
    returns count 0, whereas the grouping-key form returns nothing."""
    q = _counts_query(["Metabolite"], "(n:`{}`)", "l")
    assert "WITH count(n) AS count" in q
    # The name must NOT sit in the same clause as the aggregation.
    assert "RETURN $l0 AS name, count(n)" not in q


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    async def data(self):  # awaitable, exactly like neo4j's AsyncResult.data
        return self._rows


class FakeTx:
    """Records queries; returns rows whose keyed substring matches the query."""

    def __init__(self, rows_by_substring=None, default_rows=None):
        self.rows_by_substring = rows_by_substring or {}
        self.default_rows = [] if default_rows is None else default_rows
        self.queries = []

    async def run(self, query, **kwargs):  # coroutine, like AsyncTransaction.run
        self.queries.append(query)
        for substring, rows in self.rows_by_substring.items():
            if substring in query:
                return FakeResult(rows)
        return FakeResult(self.default_rows)


class FakeSession:
    def __init__(self, tx):
        self._tx = tx

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute_read(self, txfunc, *args, **kwargs):
        # Mirror the real driver: it AWAITS the transaction function's result.
        return await txfunc(self._tx, *args, **kwargs)


class FakeDriver:
    def __init__(self, tx):
        self._tx = tx
        self.closed = False

    def session(self, database=None):
        self.last_database = database
        return FakeSession(self._tx)

    async def close(self):
        self.closed = True


@pytest.fixture
def patch_driver(monkeypatch):
    """Patch AsyncGraphDatabase.driver to hand back a driver wrapping `tx`."""

    def _install(tx):
        driver = FakeDriver(tx)
        monkeypatch.setattr(
            cypher_mod,
            "AsyncGraphDatabase",
            SimpleNamespace(driver=lambda *a, **k: driver),
        )
        return driver

    return _install


@pytest.fixture
def tools():
    return register_cypher_tools(
        app=None,
        neo4j_uri="bolt://x:7687",
        neo4j_user="neo4j",
        neo4j_password="pw",
        default_database="graphomics",
        return_callables=True,
    )


async def test_fetch_data_awaits_both_coroutines():
    """_fetch_data must await tx.run and result.data (the GPV-400 contract)."""
    tx = FakeTx(default_rows=[{"n": 1}])
    rows = await _fetch_data(tx, "RETURN 1")
    assert rows == [{"n": 1}]


async def test_query_graph_returns_rows_not_coroutine_error(tools, patch_driver):
    driver = patch_driver(FakeTx(default_rows=[{"name": "Faecalibacterium"}]))
    out = await tools["query_graph"]("MATCH (n) RETURN n", database="micromap")
    assert "error" not in out, out
    assert out == {"rows": [{"name": "Faecalibacterium"}], "count": 1}
    assert driver.closed is True
    assert driver.last_database == "micromap"


async def test_list_databases_returns_rows(tools, patch_driver):
    rows = [{"name": "micromap", "currentStatus": "online", "type": "standard"}]
    patch_driver(FakeTx(rows_by_substring={"SHOW DATABASES": rows}))
    out = await tools["list_databases"]()
    assert out == {"databases": rows}


async def test_get_schema_constituent_branch_returns_counts(tools, patch_driver):
    # #320: each label/type carries a node count, and entries sort by count
    # desc — so a zero-node tombstone (Metabolite) is visibly empty, not a
    # bare name that reads as queryable.
    tx = FakeTx(rows_by_substring={
        "db.labels": [{"label": "Taxon"}, {"label": "Metabolite"}, {"label": "Disease"}],
        "db.relationshipTypes": [
            {"relationshipType": "PRODUCES"}, {"relationshipType": "SUPERSEDED_BY"},
        ],
        "count(n) AS count": [
            {"name": "Taxon", "count": 1150388},
            {"name": "Metabolite", "count": 0},
            {"name": "Disease", "count": 464},
        ],
        "count(r) AS count": [
            {"name": "PRODUCES", "count": 231556},
            {"name": "SUPERSEDED_BY", "count": 0},
        ],
    })
    patch_driver(tx)
    out = await tools["get_schema"](database="micromap")
    assert out["database"] == "micromap"
    assert out["labels"] == [
        {"label": "Taxon", "nodes": 1150388},
        {"label": "Disease", "nodes": 464},
        {"label": "Metabolite", "nodes": 0},
    ]
    assert out["relationship_types"] == [
        {"type": "PRODUCES", "count": 231556},
        {"type": "SUPERSEDED_BY", "count": 0},
    ]


async def test_get_schema_zero_node_label_is_visibly_empty(tools, patch_driver):
    tx = FakeTx(rows_by_substring={
        "db.labels": [{"label": "Metabolite"}],
        "db.relationshipTypes": [],
        "count(n) AS count": [{"name": "Metabolite", "count": 0}],
    })
    patch_driver(tx)
    out = await tools["get_schema"](database="micromap")
    assert out["labels"] == [{"label": "Metabolite", "nodes": 0}]


async def test_get_schema_entries_always_carry_counts(tools, patch_driver):
    """Structural ratchet (#320): get_schema must never regress to bare names —
    a bare name reads as 'queryable' even for a zero-node tombstone, the silent-
    empty failure mode. Every label entry carries an int `nodes`, every
    relationship entry an int `count`."""
    tx = FakeTx(rows_by_substring={
        "db.labels": [{"label": "Taxon"}, {"label": "Metabolite"}],
        "db.relationshipTypes": [{"relationshipType": "PRODUCES"}],
        "count(n) AS count": [
            {"name": "Taxon", "count": 1150388}, {"name": "Metabolite", "count": 0},
        ],
        "count(r) AS count": [{"name": "PRODUCES", "count": 231556}],
    })
    patch_driver(tx)
    out = await tools["get_schema"](database="micromap")
    for entry in out["labels"]:
        assert isinstance(entry, dict) and isinstance(entry.get("nodes"), int)
    for entry in out["relationship_types"]:
        assert isinstance(entry, dict) and isinstance(entry.get("count"), int)


async def test_get_schema_empty_label_set_runs_no_count_query(tools, patch_driver):
    tx = FakeTx(rows_by_substring={"db.labels": [], "db.relationshipTypes": []})
    patch_driver(tx)
    out = await tools["get_schema"](database="micromap")
    assert out["labels"] == []
    assert out["relationship_types"] == []
    # No count query issued when there is nothing to count (avoids an empty UNION).
    assert not any("count(n)" in q for q in tx.queries)
    assert not any("count(r)" in q for q in tx.queries)


#: A SHOW DATABASES row as the server returns it for the composite — constituents
#: are prefixed `graphomics.` and now include primekg (federated in after the old
#: hardcoded list was written, #275).
_COMPOSITE_SHOW_ROW = [{
    "name": "graphomics", "type": "composite",
    "constituents": [
        "graphomics.micromap", "graphomics.genomics", "graphomics.transcriptomics",
        "graphomics.metabolomics", "graphomics.proteomics", "graphomics.primekg",
    ],
}]


async def test_get_schema_composite_branch_returns_counts(tools, patch_driver):
    tx = FakeTx(rows_by_substring={
        "SHOW DATABASES": _COMPOSITE_SHOW_ROW,
        "db.labels": [{"label": "Taxon"}],
        "db.relationshipTypes": [{"relationshipType": "PRODUCES"}],
        "count(n) AS count": [{"name": "Taxon", "count": 1150388}],
        "count(r) AS count": [{"name": "PRODUCES", "count": 231556}],
    })
    patch_driver(tx)
    out = await tools["get_schema"]()  # default "graphomics" → composite
    assert out["database"] == "graphomics"
    # #275: the now-federated primekg must appear — the old hardcoded list omitted it.
    assert set(out["constituents"]) == {
        "micromap", "genomics", "transcriptomics", "metabolomics", "proteomics",
        "primekg",
    }
    assert out["constituents"]["micromap"]["labels"] == [
        {"label": "Taxon", "nodes": 1150388},
    ]
    assert out["constituents"]["micromap"]["relationship_types"] == [
        {"type": "PRODUCES", "count": 231556},
    ]
    # Guard: every issued query must have balanced braces (see #320 canary note).
    for q in tx.queries:
        assert q.count("{") == q.count("}"), f"unbalanced braces: {q!r}"
        assert "}}" not in q and "{{" not in q, f"double brace leaked: {q!r}"


async def test_get_schema_composite_constituents_are_derived_from_the_db(tools, patch_driver):
    """REGRESSION (#275): the constituent set must come from SHOW DATABASES, never
    a hardcoded literal. primekg was federated in and the hardcoded list silently
    dropped it. A different server-reported set must be reflected verbatim —
    including a constituent name the code has never heard of."""
    tx = FakeTx(rows_by_substring={
        "SHOW DATABASES": [{
            "name": "graphomics", "type": "composite",
            "constituents": ["graphomics.micromap", "graphomics.primekg",
                             "graphomics.brandnew"],
        }],
        "db.labels": [],
        "db.relationshipTypes": [],
    })
    patch_driver(tx)
    out = await tools["get_schema"]()
    assert set(out["constituents"]) == {"micromap", "primekg", "brandnew"}, (
        "constituents must be derived from SHOW DATABASES, not a hardcoded list"
    )
    assert any("SHOW DATABASES" in q for q in tx.queries), (
        "get_schema must ask the server for the composite's constituents"
    )
    # Guard: every issued query must have balanced braces. The composite
    # introspection wraps db.labels()/db.relationshipTypes() in CALL { USE ... },
    # and a split-string `}}` (literal in a non-f segment) produced an invalid
    # `... }} RETURN` that only the live composite canary caught — the FakeTx
    # matched on substring and never noticed. Balanced braces catches it here.
    for q in tx.queries:
        assert q.count("{") == q.count("}"), f"unbalanced braces: {q!r}"
        assert "}}" not in q and "{{" not in q, f"double brace leaked: {q!r}"


def _as(principal):
    """Patch current_principal for the duration of a call."""
    return patch("micromap_mcp.tools.cypher.current_principal", return_value=principal)


async def test_non_privileged_caller_is_refused_before_reaching_neo4j(
    tools, patch_driver, monkeypatch
):
    monkeypatch.setenv("MCP_TOKEN_ORGS", "tok:acme:user")   # identity configured
    tx = FakeTx({"MATCH": [{"n": 1}]})
    patch_driver(tx)
    with _as(Principal(org_id="acme", role="user")):
        out = await tools["query_graph"]("MATCH (n) RETURN n")
    assert out["rows"] == [] and out["count"] == 0
    assert "kg_" in out["error"], "refusal must name the structured tools"
    assert tx.queries == [], "the query must not reach Neo4j at all"


async def test_service_principal_is_allowed(tools, patch_driver, monkeypatch):
    monkeypatch.setenv("MCP_TOKEN_ORGS", "tok:graphomics:service")
    tx = FakeTx({"RETURN 1": [{"n": 1}]})
    patch_driver(tx)
    with _as(Principal(org_id="graphomics", role="service")):
        out = await tools["query_graph"]("RETURN 1")
    assert out["rows"] == [{"n": 1}]


@pytest.mark.parametrize("role", ["service", "admin", "Admin", " service "])
async def test_privileged_roles_are_all_permitted_through_the_gate(
    tools, patch_driver, monkeypatch, role
):
    """#299 final review item 6: only "service" was previously exercised on
    this gate. `PRIVILEGED_ROLES` also includes "admin", and the gate
    normalizes via `.strip().lower()` (`_cypher_refusal`) — cover both, plus
    the exact `PRIVILEGED_ROLES` membership, in one parametrization."""
    monkeypatch.setenv("MCP_TOKEN_ORGS", "tok:graphomics:service")
    tx = FakeTx({"RETURN 1": [{"n": 1}]})
    patch_driver(tx)
    with _as(Principal(org_id="graphomics", role=role)):
        out = await tools["query_graph"]("RETURN 1")
    assert out["rows"] == [{"n": 1}], out


async def test_non_privileged_role_is_the_control_for_the_privileged_parametrization(
    tools, patch_driver, monkeypatch
):
    """Control for the parametrized test above: a role that is not in
    PRIVILEGED_ROLES (and isn't just a case/whitespace variant of one) must
    still be refused."""
    monkeypatch.setenv("MCP_TOKEN_ORGS", "tok:acme:member")
    tx = FakeTx({"RETURN 1": [{"n": 1}]})
    patch_driver(tx)
    with _as(Principal(org_id="acme", role="member")):
        out = await tools["query_graph"]("RETURN 1")
    assert out["rows"] == [] and out["count"] == 0


async def test_gate_is_inactive_until_identity_is_configured(
    tools, patch_driver, monkeypatch
):
    """Compatibility contract: with no map and no JWT, behaviour is unchanged."""
    monkeypatch.delenv("MCP_TOKEN_ORGS", raising=False)
    for k in ("JWT_SECRET", "JWT_PUBLIC_KEY", "JWKS_URL"):
        monkeypatch.delenv(k, raising=False)
    tx = FakeTx({"RETURN 1": [{"n": 1}]})
    patch_driver(tx)
    with _as(Principal(org_id="default", role="")):
        out = await tools["query_graph"]("RETURN 1")
    assert out["rows"] == [{"n": 1}]


def test_query_graph_description_is_honest_about_empty_constituents(tools):
    """#275: the tool description an LLM reads must not promise data the empty
    constituents don't hold. It previously listed genomics/transcriptomics/
    metabolomics/proteomics as if populated; all four are 0 nodes. Guard so it
    can't silently regress to the misleading version."""
    desc = tools["query_graph"].__doc__
    assert "currently empty" in desc
    for empty in ("genomics", "transcriptomics", "metabolomics", "proteomics"):
        assert empty in desc
    # steer the caller to the count-bearing get_schema (#320) rather than
    # inferring emptiness from an ambiguous empty result set
    assert "get_schema" in desc


async def test_list_databases_stays_open_to_everyone(
    tools, patch_driver, monkeypatch
):
    """It exposes names, not data, and is needed to use the kg_* tools."""
    monkeypatch.setenv("MCP_TOKEN_ORGS", "tok:acme:user")
    tx = FakeTx({"SHOW DATABASES": [{"name": "micromap"}]})
    patch_driver(tx)
    with _as(Principal(org_id="acme", role="user")):
        out = await tools["list_databases"]()
    assert "error" not in out
