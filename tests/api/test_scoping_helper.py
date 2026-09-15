import pytest
from api.scoping import (
    OrgScope,
    ORG_FILTER,
    EDGE_ORG_FILTER,
    scope_params,
    resolve_org_scope,
)


def test_org_filter_fragment_references_both_params():
    frag = ORG_FILTER("t")
    assert frag == "(t.organization_id = $organization_id OR t.organization_id IN $public_orgs)"


def test_edge_org_filter_is_null_tolerant():
    """#297: Unlike the node filter, the edge filter must treat a NULL org as shared:
    ingested/reference edges (GMRepo/Disbiome) carry no organization_id and must
    stay visible to every caller. Reuses the same $organization_id/$public_orgs
    params as ORG_FILTER, so no new plumbing."""
    frag = EDGE_ORG_FILTER("r")
    assert frag == (
        "(r.organization_id IS NULL "
        "OR r.organization_id = $organization_id "
        "OR r.organization_id IN $public_orgs)"
    )


def test_edge_org_filter_and_node_filter_share_params():
    # both reference exactly $organization_id and $public_orgs -> scope_params() feeds both
    for frag in (ORG_FILTER("t"), EDGE_ORG_FILTER("r")):
        assert "$organization_id" in frag and "$public_orgs" in frag


def test_scope_params_serializes_org_and_public_orgs():
    scope = OrgScope(org_id="acme", public_orgs=["default"])
    assert scope_params(scope) == {"organization_id": "acme", "public_orgs": ["default"]}


@pytest.mark.asyncio
async def test_resolve_org_scope_uses_default_public_orgs(monkeypatch):
    monkeypatch.delenv("PUBLIC_ORGS", raising=False)
    monkeypatch.delenv("API_KEY_ORGS", raising=False)
    monkeypatch.delenv("DEFAULT_ORG", raising=False)
    scope = await resolve_org_scope(x_api_key=None, authorization=None)
    assert scope.public_orgs == ["default"]
    assert scope.org_id == "default"  # unauthenticated -> shared org


@pytest.mark.asyncio
async def test_resolve_org_scope_derives_org_from_api_key(monkeypatch):
    monkeypatch.setenv("API_KEY_ORGS", "key_acme:acme")
    monkeypatch.setenv("PUBLIC_ORGS", "default,reference")
    scope = await resolve_org_scope(x_api_key="key_acme", authorization=None)
    assert scope.org_id == "acme"
    assert scope.public_orgs == ["default", "reference"]


@pytest.mark.asyncio
async def test_default_org_fallback_is_default(monkeypatch):
    """#200: the org fallback must be 'default' (matches the loader stamp), not
    'demo' — otherwise a dev/unauthenticated caller would see no reference KG."""
    from api.dependencies import resolve_organization_id

    monkeypatch.delenv("DEFAULT_ORG", raising=False)
    monkeypatch.delenv("API_KEY_ORGS", raising=False)
    assert await resolve_organization_id(x_api_key=None, authorization=None) == "default"
    assert await resolve_organization_id(x_api_key="unmapped-key", authorization=None) == "default"
