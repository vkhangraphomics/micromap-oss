import asyncio
import pytest
from micromap_mcp.server import build_app


def test_build_app_returns_a_fastmcp_instance(env):
    app = build_app()
    assert app.name == "graphomics-kg-mcp"


def test_app_registers_kg_and_mapforge_tools(env):
    app = build_app()
    tools = asyncio.run(app.get_tools())  # fastmcp 2.13.3 uses get_tools() (returns dict)
    # Tool count: 3 cypher tools (query_graph, get_schema, list_databases) +
    # 10 mapforge tools + 2 provenance tools + 10 kg tools registered by #299
    # as the non-privileged read surface + 2 write tools (#268). Bump deliberately.
    assert len(tools) == 27
    names = set(tools)
    assert "query_graph" in names
    assert "get_schema" in names
    assert "list_databases" in names
    assert "mapforge_create_bundle" in names
    assert "mapforge_submit" in names
    assert "mapforge_templates_list" in names
    assert "mapforge_templates_show" in names
    assert "provenance_record_finding" in names
    assert "provenance_lineage" in names
    assert "kg_search" in names
    assert "kg_taxon_by_name" in names
    assert "graph_delete" in names           # #268 admin-only write surface
    assert "graph_set_property" in names


def test_build_app_requires_an_auth_source(monkeypatch, env):
    monkeypatch.delenv("MICROMAP_MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("MCP_TOKEN_ORGS", raising=False)
    for k in ("JWT_SECRET", "JWT_PUBLIC_KEY", "JWKS_URL"):
        monkeypatch.delenv(k, raising=False)
    from micromap_mcp.config import Settings
    with pytest.raises(RuntimeError, match="no auth source"):
        build_app(Settings())


def test_build_app_ok_with_jwks_only(monkeypatch, env):
    monkeypatch.delenv("MICROMAP_MCP_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("JWKS_URL", "https://workbench/oauth2/jwks")
    from micromap_mcp.config import Settings
    app = build_app(Settings())
    assert app.name


def test_build_app_ok_with_token_orgs_only(monkeypatch, env):
    """The rollout target this branch is meant to support: per-caller tokens
    minted via MCP_TOKEN_ORGS, no legacy shared token, no JWT. auth.py's own
    tested contract (test_token_in_map_authenticates_even_without_legacy_token)
    already says this works — build_app must not refuse to start here."""
    monkeypatch.delenv("MICROMAP_MCP_AUTH_TOKEN", raising=False)
    for k in ("JWT_SECRET", "JWT_PUBLIC_KEY", "JWKS_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MCP_TOKEN_ORGS", "tok_a:acme:service")
    from micromap_mcp.config import Settings
    app = build_app(Settings())
    assert app.name


def test_warns_loudly_when_identity_is_not_configured(monkeypatch, env, caplog):
    monkeypatch.delenv("MCP_TOKEN_ORGS", raising=False)
    monkeypatch.delenv("MCP_DEFAULT_ORG", raising=False)
    for k in ("JWT_SECRET", "JWT_PUBLIC_KEY", "JWKS_URL"):
        monkeypatch.delenv(k, raising=False)
    with caplog.at_level("WARNING"):
        build_app()
    assert "MCP_TOKEN_ORGS" in caplog.text
    assert "query_graph" in caplog.text
    # Item 5: the warning must name the org unconfigured writes land in and
    # say it is canonical, so an operator understands writes in this state
    # project into the shared reference graph every tenant reads.
    assert "default" in caplog.text
    assert "canonical" in caplog.text


def test_org_api_keys_gap_warning_fires_when_org_is_unmapped(monkeypatch, env, caplog):
    """Item 3: MCP_TOKEN_ORGS names an org with no MCP_ORG_API_KEYS entry —
    that org's kg_* reads silently fall back to the server's default REST
    credential. The server must warn (org names only, never a token/key)."""
    monkeypatch.setenv("MCP_TOKEN_ORGS", "shh-secret-caller-token:acme:service")
    monkeypatch.delenv("MCP_ORG_API_KEYS", raising=False)
    with caplog.at_level("WARNING"):
        build_app()
    assert "MCP_ORG_API_KEYS" in caplog.text
    assert "acme" in caplog.text
    assert "shh-secret-caller-token" not in caplog.text  # never log the token value


def test_org_api_keys_gap_warning_silent_when_all_orgs_mapped(monkeypatch, env, caplog):
    monkeypatch.setenv("MCP_TOKEN_ORGS", "shh-secret-caller-token:acme:service")
    monkeypatch.setenv("MCP_ORG_API_KEYS", "acme:rk_acme")
    with caplog.at_level("WARNING"):
        build_app()
    assert "MCP_ORG_API_KEYS has no entry" not in caplog.text
    assert "rk_acme" not in caplog.text  # never log the REST key value


def test_no_warning_once_identity_is_configured(monkeypatch, env, caplog):
    monkeypatch.setenv("MCP_TOKEN_ORGS", "tok:graphomics:service")
    # Also map the org so the separate MCP_ORG_API_KEYS-gap warning (item 3)
    # doesn't fire and confuse this assertion, which is only about the
    # IDENTITY NOT CONFIGURED warning being suppressed.
    monkeypatch.setenv("MCP_ORG_API_KEYS", "graphomics:rk_graphomics")
    with caplog.at_level("WARNING"):
        build_app()
    assert "IDENTITY NOT CONFIGURED" not in caplog.text
