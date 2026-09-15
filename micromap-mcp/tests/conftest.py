import pytest


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("MICROMAP_KG_URL", "https://kgdev.example.com")
    monkeypatch.setenv("MICROMAP_KG_API_KEY", "kg-key")
    monkeypatch.setenv("MICROMAP_MCP_AUTH_TOKEN", "incoming-bearer")
    monkeypatch.setenv("NEO4J_URI", "bolt://neo4j:7687")
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "pw")
    # BundleDirManager tries to mkdir the base on construction; point it at
    # a real writable directory so build_app() and the tools fixture work on
    # any platform (including Windows where /data/mcp-bundles doesn't exist).
    bundle_base = tmp_path / "mcp-bundles"
    bundle_base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MAPFORGE_BUNDLE_BASE", str(bundle_base))
