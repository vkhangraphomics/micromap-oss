"""FastMCP entry point. Workbench-OIDC JWT (JWKS) auth with a static-bearer
fallback during migration — see micromap_mcp/auth.py."""
import logging

from fastmcp import FastMCP

from .auth import (
    WorkbenchJWKSVerifier,
    default_org,
    identity_configured,
    jwt_enabled,
    org_api_keys,
    token_orgs,
)
from .config import Settings
from .kg_client import KGClient
from .mapforge_runner import MapForgeRunner
from .tools.cypher import register_cypher_tools
from .tools.kg import register_kg_tools
from .tools.mapforge import register_mapforge_tools
from .tools.provenance import register_provenance_tools
from .tools.write import register_write_tools

logger = logging.getLogger(__name__)


def build_app(settings: Settings | None = None) -> FastMCP:
    s = settings or Settings()

    static_token = (
        s.micromap_mcp_auth_token.get_secret_value()
        if s.micromap_mcp_auth_token is not None
        else None
    )
    if not jwt_enabled() and not static_token and not token_orgs():
        raise RuntimeError(
            "no auth source configured: set JWKS_URL/JWT_PUBLIC_KEY/JWT_SECRET, "
            "MICROMAP_MCP_AUTH_TOKEN, or MCP_TOKEN_ORGS"
        )

    auth = WorkbenchJWKSVerifier(static_token=static_token)
    app = FastMCP(name="graphomics-kg-mcp", auth=auth)

    if not identity_configured():
        logger.warning(
            "IDENTITY NOT CONFIGURED: every caller shares one token, so the "
            "org boundary is NOT enforced and query_graph is open to all "
            "callers. Every write in this state lands in org %r (MCP_DEFAULT_ORG), "
            "which canonical_orgs() treats as canonical by default — so those "
            "findings project into the shared reference graph every tenant reads. "
            "Set MCP_TOKEN_ORGS (token:org:role) to activate enforcement — see "
            "docs/specs/2026-07-27-mcp-org-scoping-design.md section 8. (#299)",
            default_org(),
        )

    # Org boundary can degrade silently: an org present in MCP_TOKEN_ORGS but
    # absent from MCP_ORG_API_KEYS still authenticates fine, but every kg_*
    # read for that org falls back to the server's own default REST credential
    # (KGClient.get's api_key=None path) — no error, no refusal, just reading
    # through the wrong credential. identity_configured() is true in this
    # state, so the warning above is suppressed; this check catches the gap
    # it leaves (#299 final review). Org names only — never a token/key value.
    unmapped_orgs = sorted({org for org, _role in token_orgs().values()} - set(org_api_keys()))
    if unmapped_orgs:
        logger.warning(
            "MCP_ORG_API_KEYS has no entry for org(s) %s (mapped in "
            "MCP_TOKEN_ORGS): kg_* reads for these orgs will silently use the "
            "server's default REST credential instead of a caller-scoped one. "
            "Set MCP_ORG_API_KEYS to close the gap. (#299)",
            ", ".join(unmapped_orgs),
        )

    register_cypher_tools(
        app=app,
        neo4j_uri=s.neo4j_uri,
        neo4j_user=s.neo4j_user,
        neo4j_password=s.neo4j_password.get_secret_value(),
        default_database=s.neo4j_database,
    )

    register_kg_tools(
        app=app,
        client=KGClient(
            base_url=s.micromap_kg_url,
            api_key=(s.micromap_kg_api_key.get_secret_value()
                     if s.micromap_kg_api_key is not None else ""),
        ),
    )

    runner = MapForgeRunner()
    register_mapforge_tools(
        app=app,
        runner=runner,
        neo4j_uri=s.neo4j_uri,
        neo4j_user=s.neo4j_user,
        neo4j_password=s.neo4j_password.get_secret_value(),
        neo4j_database=s.neo4j_database,
        bundle_base=s.mapforge_bundle_base,
    )

    register_provenance_tools(
        app=app,
        neo4j_uri=s.neo4j_uri,
        neo4j_user=s.neo4j_user,
        neo4j_password=s.neo4j_password.get_secret_value(),
        database=s.provenance_write_database,
    )

    # Admin-only write surface (graph_delete / graph_set_property, #268). Refused
    # unless identity is configured AND the caller is admin — fail-closed, the
    # opposite of the read gate. Writes target the constituent that holds the
    # data, never the composite (which is not writable — see the Fabric note).
    register_write_tools(
        app=app,
        neo4j_uri=s.neo4j_uri,
        neo4j_user=s.neo4j_user,
        neo4j_password=s.neo4j_password.get_secret_value(),
        database=s.provenance_write_database,
    )
    return app


if __name__ == "__main__":
    s = Settings()
    app = build_app(s)
    app.run(transport="streamable-http", host="0.0.0.0", port=s.mcp_port)
