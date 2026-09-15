import pytest
import httpx
import respx
from micromap_mcp.kg_client import KGClient
from micromap_mcp.tools.kg import register_kg_tools


KG = "https://kgdev.example.com"


@pytest.fixture
async def kg_client(env):
    c = KGClient(base_url=KG, api_key="kg-key", timeout_seconds=5)
    yield c
    await c.aclose()


@pytest.fixture
def tools(kg_client, env):
    # Test by invoking the underlying callables, not via the FastMCP transport.
    return register_kg_tools(app=None, client=kg_client, return_callables=True)


# (tool_name, kwargs, expected_path, expected_qs)
CASES = [
    ("kg_search",            dict(q="lacto", limit=5),
        "/api/v1/search", {"q": "lacto", "limit": "5"}),
    ("kg_taxon_by_id",       dict(taxon_id="239935"),
        "/api/v1/taxa/239935", {}),
    ("kg_taxon_by_name",     dict(query="Faecalibacterium"),
        "/api/v1/taxa/search/Faecalibacterium", {}),
    ("kg_taxon_diseases",    dict(taxon_id="239935", limit=10),
        "/api/v1/taxa/239935/diseases", {"limit": "10"}),
    ("kg_taxon_metabolites", dict(taxon_id="239935"),
        "/api/v1/taxa/239935/metabolites", {}),
    ("kg_disease_taxa",      dict(disease_id="obesity", direction="depleted"),
        "/api/v1/diseases/obesity/taxa", {"direction": "depleted"}),
    ("kg_disease_metabolites", dict(disease_id="obesity"),
        "/api/v1/diseases/obesity/metabolites", {}),
    ("kg_metabolite_producers", dict(metabolite_id="butyrate", limit=20),
        "/api/v1/metabolites/butyrate/producers", {"limit": "20"}),
    ("kg_graph_neighborhood", dict(entity_id="Faecalibacterium prausnitzii", max_depth=1, limit=8),
        "/api/v1/graph/neighborhood", {"entity_id": "Faecalibacterium prausnitzii", "max_depth": "1", "limit": "8"}),
    ("kg_graph_path",         dict(from_id="Faecalibacterium prausnitzii", to_id="Butyrate"),
        "/api/v1/graph/path", {"from_id": "Faecalibacterium prausnitzii", "to_id": "Butyrate"}),
]


@respx.mock
@pytest.mark.parametrize("tool_name,kwargs,expected_path,expected_qs", CASES)
async def test_each_kg_tool_calls_right_url(tools, tool_name, kwargs, expected_path, expected_qs):
    respx.get(f"{KG}{expected_path}").mock(return_value=httpx.Response(200, json={"ok": True}))
    tool = tools[tool_name]
    res = await tool(**kwargs)
    assert res == {"ok": True}
    call = respx.calls.last
    for k, v in expected_qs.items():
        assert call.request.url.params[k] == v


from unittest.mock import patch

from micromap_mcp.auth import Principal


class RecordingClient:
    """Minimal stand-in that records the api_key each tool passes through."""

    def __init__(self):
        self.calls = []

    async def get(self, path, params=None, api_key=None):
        self.calls.append({"path": path, "params": params, "api_key": api_key})
        return {"ok": True}


async def test_kg_tool_sends_the_callers_mapped_rest_key(monkeypatch, env):
    monkeypatch.setenv("MCP_ORG_API_KEYS", "acme:rk_acme,beta:rk_beta")
    client = RecordingClient()
    callables = register_kg_tools(app=None, client=client, return_callables=True)
    with patch("micromap_mcp.tools.kg.current_principal",
               return_value=Principal(org_id="acme", role="user")):
        await callables["kg_search"](q="butyrate")
    assert client.calls[-1]["api_key"] == "rk_acme"


async def test_kg_tool_falls_back_to_default_key_when_org_unmapped(monkeypatch, env):
    monkeypatch.setenv("MCP_ORG_API_KEYS", "acme:rk_acme")
    client = RecordingClient()
    callables = register_kg_tools(app=None, client=client, return_callables=True)
    with patch("micromap_mcp.tools.kg.current_principal",
               return_value=Principal(org_id="unmapped", role="user")):
        await callables["kg_search"](q="butyrate")
    # None => the client's constructor key applies, and REST scopes by that key.
    assert client.calls[-1]["api_key"] is None


@respx.mock
async def test_kg_client_per_request_key_overrides_the_constructor_key(env):
    route = respx.get(f"{KG}/api/v1/search").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    c = KGClient(base_url=KG, api_key="default-key", timeout_seconds=5)
    try:
        await c.get("/api/v1/search", api_key="rk_acme")
    finally:
        await c.aclose()
    assert route.calls.last.request.headers["X-API-Key"] == "rk_acme"


@respx.mock
async def test_kg_taxon_by_name_escapes_path_traversal(tools):
    """A caller-supplied value must not be able to walk the request path out of
    `/api/v1/taxa/search/` onto an arbitrary REST endpoint (#299 final review,
    Finding 1). Unescaped, httpx normalizes `..` dot-segments when it builds
    the request, so `"../../admin"` would resolve to `/api/admin`. Assert on
    the actual outbound request path, not a fake's recorded argument, so the
    test exercises httpx's real URL-building behavior."""
    route = respx.route(method="GET").mock(return_value=httpx.Response(200, json={"ok": True}))
    await tools["kg_taxon_by_name"](query="../../admin")
    path = route.calls.last.request.url.path
    assert path.startswith("/api/v1/taxa/search/"), path


@respx.mock
async def test_kg_metabolite_producers_escapes_query_injection(tools):
    """A `?` in a caller-supplied id must not inject a new query parameter —
    only the params the tool itself set (`limit`) may appear (#299 final
    review, Finding 1)."""
    route = respx.route(method="GET").mock(return_value=httpx.Response(200, json={"ok": True}))
    await tools["kg_metabolite_producers"](metabolite_id="butyrate?evil=1", limit=20)
    request = route.calls.last.request
    assert dict(request.url.params) == {"limit": "20"}


@respx.mock
async def test_kg_client_without_a_per_request_key_keeps_the_constructor_key(env):
    """When no per-request api_key is provided, the outbound request carries the
    constructor key. This property is relied upon for unmapped orgs that fall back
    to the default key."""
    route = respx.get(f"{KG}/api/v1/search").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    c = KGClient(base_url=KG, api_key="default-key", timeout_seconds=5)
    try:
        # Call without api_key argument
        await c.get("/api/v1/search")
        assert route.calls[-1].request.headers["X-API-Key"] == "default-key"

        # Call with explicit api_key=None
        await c.get("/api/v1/search", api_key=None)
        assert route.calls[-1].request.headers["X-API-Key"] == "default-key"
    finally:
        await c.aclose()
