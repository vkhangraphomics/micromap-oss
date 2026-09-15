import pytest


def _set_required(monkeypatch):
    monkeypatch.setenv("MICROMAP_MCP_AUTH_TOKEN", "incoming-bearer")
    monkeypatch.setenv("NEO4J_URI", "bolt://neo4j:7687")
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "pw")


def test_settings_loads_required_and_defaults(monkeypatch):
    _set_required(monkeypatch)
    from micromap_mcp.config import Settings
    s = Settings()
    assert s.micromap_mcp_auth_token.get_secret_value() == "incoming-bearer"
    assert s.neo4j_database == "graphomics"
    assert s.mcp_port == 8200
    assert s.mapforge_bundle_base == "/data/mcp-bundles"
    assert s.bundle_gc_age_days == 7


def test_settings_raises_when_required_missing(monkeypatch):
    for var in ("MICROMAP_MCP_AUTH_TOKEN", "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    from pydantic import ValidationError
    from micromap_mcp.config import Settings
    with pytest.raises(ValidationError):
        Settings()


def test_secret_str_does_not_leak_via_repr(monkeypatch):
    _set_required(monkeypatch)
    from micromap_mcp.config import Settings
    s = Settings()
    assert "incoming-bearer" not in repr(s)
    assert "pw" not in repr(s)


def test_auth_token_optional(monkeypatch):
    monkeypatch.delenv("MICROMAP_MCP_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("NEO4J_URI", "bolt://x:7687")
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "pw")
    from micromap_mcp.config import Settings
    s = Settings()
    assert s.micromap_mcp_auth_token is None
