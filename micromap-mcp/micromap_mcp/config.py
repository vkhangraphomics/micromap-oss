"""Env-driven settings for micromap-mcp. Secrets use SecretStr so reprs are safe."""
from typing import Optional

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False)

    # Inbound Bearer auth presented by Nexus (optional once JWKS is configured;
    # build_app requires at least one of this or JWT material — see server.py).
    micromap_mcp_auth_token: Optional[SecretStr] = None

    # MapForge submit target (bolt)
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: SecretStr
    neo4j_database: str = "graphomics"

    # Server
    mcp_port: int = 8200

    # Bundle storage + GC
    mapforge_bundle_base: str = "/data/mcp-bundles"
    bundle_gc_age_days: int = 7

    # GPV-254: constituent DB the provenance spine writes to (composite is not writable)
    provenance_write_database: str = "neo4j"

    # REST API the kg_* tools proxy to (#299 registered them; see tools/kg.py).
    micromap_kg_url: str = "http://micromap-api:8200"
    micromap_kg_api_key: Optional[SecretStr] = None
