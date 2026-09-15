import pytest
import httpx
import respx
from micromap_mcp.kg_client import (
    KGClient, KGAuthError, KGNotFoundError, KGRateLimitError, KGServerError,
)


@pytest.fixture
async def client():
    c = KGClient(base_url="https://kgdev.example.com", api_key="test-key", timeout_seconds=5.0)
    yield c
    await c.aclose()


@respx.mock
async def test_get_sends_api_key_header_and_returns_json(client):
    route = respx.get("https://kgdev.example.com/api/v1/stats").mock(
        return_value=httpx.Response(200, json={"total_nodes": 1128876})
    )
    res = await client.get("/api/v1/stats")
    assert res == {"total_nodes": 1128876}
    assert route.called
    sent = route.calls[0].request
    assert sent.headers["x-api-key"] == "test-key"


@respx.mock
async def test_get_drops_none_query_params(client):
    route = respx.get("https://kgdev.example.com/api/v1/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    await client.get("/api/v1/search", params={"q": "lacto", "types": None, "limit": 5})
    sent = route.calls[0].request
    assert "types" not in sent.url.params
    assert sent.url.params["q"] == "lacto"
    assert sent.url.params["limit"] == "5"


@respx.mock
@pytest.mark.parametrize("status,exc", [
    (401, KGAuthError),
    (404, KGNotFoundError),
    (429, KGRateLimitError),
    (500, KGServerError),
    (503, KGServerError),
])
async def test_typed_errors_per_status(client, status, exc):
    respx.get("https://kgdev.example.com/api/v1/x").mock(
        return_value=httpx.Response(status, json={"detail": "boom"})
    )
    with pytest.raises(exc):
        await client.get("/api/v1/x")
