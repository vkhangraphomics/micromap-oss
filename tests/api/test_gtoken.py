"""GToken deduction client (GPV-410 / #232 work item D).

Verifies the client posts Workbench's actual DeductTokensRequest contract
(camelCase, orgId in body), is fail-open (never raises; None on
error/402/disabled), and skips non-numeric orgs.
"""
import asyncio
import json

import httpx
import respx

from api.gtoken import GTokenClient, RESOURCE_CONTRIBUTION

BASE = "https://wb.example/auth/api/v3"
DEDUCT = f"{BASE}/internal/tokens/deduct"


def test_disabled_when_no_url(monkeypatch):
    monkeypatch.delenv("GTOKEN_API_URL", raising=False)
    c = GTokenClient()
    assert c.disabled is True
    assert asyncio.run(c.deduct("7", RESOURCE_CONTRIBUTION)) is None


@respx.mock
def test_deduct_success_sends_workbench_contract():
    route = respx.post(DEDUCT).mock(
        return_value=httpx.Response(200, json={"newBalance": 99.0, "tokensDeducted": 1.0,
                                               "warning": False}))
    c = GTokenClient(base_url=BASE)
    out = asyncio.run(c.deduct(7, RESOURCE_CONTRIBUTION, quantity=1, unit="contribution",
                               reference="bundle-x"))
    assert out["tokensDeducted"] == 1.0
    assert route.called
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"orgId": 7, "resourceType": "micromap_contribution",
                    "quantity": 1.0, "unit": "contribution", "reference": "bundle-x"}


@respx.mock
def test_deduct_insufficient_funds_402_returns_none():
    respx.post(DEDUCT).mock(return_value=httpx.Response(402, json={}))
    assert asyncio.run(GTokenClient(base_url=BASE).deduct(7, RESOURCE_CONTRIBUTION)) is None


@respx.mock
def test_deduct_server_error_returns_none_never_raises():
    respx.post(DEDUCT).mock(return_value=httpx.Response(500, json={}))
    assert asyncio.run(GTokenClient(base_url=BASE).deduct(7, RESOURCE_CONTRIBUTION)) is None


@respx.mock
def test_deduct_network_error_returns_none():
    respx.post(DEDUCT).mock(side_effect=httpx.ConnectError("down"))
    assert asyncio.run(GTokenClient(base_url=BASE).deduct(7, RESOURCE_CONTRIBUTION)) is None


@respx.mock
def test_deduct_non_numeric_org_skips_without_call():
    route = respx.post(DEDUCT).mock(return_value=httpx.Response(200, json={}))
    # MicroMap org slug ("acme") is not a Workbench Long orgId -> skip, no HTTP.
    assert asyncio.run(GTokenClient(base_url=BASE).deduct("acme", RESOURCE_CONTRIBUTION)) is None
    assert not route.called


@respx.mock
def test_deduct_omits_reference_when_absent():
    route = respx.post(DEDUCT).mock(return_value=httpx.Response(200, json={"tokensDeducted": 1.0}))
    asyncio.run(GTokenClient(base_url=BASE).deduct(7, RESOURCE_CONTRIBUTION))
    sent = json.loads(route.calls.last.request.content)
    assert "reference" not in sent
