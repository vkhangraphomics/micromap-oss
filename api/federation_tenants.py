"""Resolve an authenticated API key to its federation write target.

FEDERATION_TENANTS is an env JSON object: api_key -> target config.

    {"key_acme": {"organization_id": "acme", "database": "acme_db",
                  "bolt_uri": "bolt://neo4j:7687", "bolt_auth_ref": "env:ACME_BOLT_PW",
                  "bolt_user": "mapforge"}}

The bundle never names the target — this server-side map is authoritative, so a
client can never aim writes at another tenant's database (spec Decisions 4).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass


class UnknownTenantError(Exception):
    """API key is not a registered federation tenant."""


@dataclass(frozen=True)
class FederationTarget:
    organization_id: str
    database: str
    bolt_uri: str
    bolt_auth_ref: str
    bolt_user: str = "mapforge"


def _load_tenants() -> dict:
    raw = os.environ.get("FEDERATION_TENANTS", "")
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"FEDERATION_TENANTS is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("FEDERATION_TENANTS must be a JSON object")
    return data


def _target_from_cfg(cfg) -> FederationTarget:
    try:
        return FederationTarget(
            organization_id=cfg["organization_id"],
            database=cfg["database"],
            bolt_uri=cfg["bolt_uri"],
            bolt_auth_ref=cfg["bolt_auth_ref"],
            bolt_user=cfg.get("bolt_user", "mapforge"),
        )
    except (KeyError, TypeError) as exc:
        raise ValueError(f"FEDERATION_TENANTS entry malformed: {exc}") from exc


def resolve_federation_target(api_key: str) -> FederationTarget:
    cfg = _load_tenants().get(api_key)
    if cfg is None:
        raise UnknownTenantError("API key is not a registered federation tenant")
    return _target_from_cfg(cfg)


def resolve_federation_target_by_org(organization_id: str) -> FederationTarget:
    """Resolve a tenant by its organization_id — the JWT path.

    A verified Workbench token carries the caller's org; this maps that org to
    its registered write target. The token selects *which* allowlisted tenant;
    it cannot introduce a new one or aim writes elsewhere (the ``database`` /
    ``bolt_uri`` stay server-side, as with the api-key path).
    """
    for cfg in _load_tenants().values():
        if isinstance(cfg, dict) and cfg.get("organization_id") == organization_id:
            return _target_from_cfg(cfg)
    raise UnknownTenantError(
        f"no federation tenant registered for organization {organization_id!r}"
    )


def resolve_bolt_password(ref: str) -> str:
    """Resolve an `env:NAME` reference to the password. Only env refs allowed."""
    if not ref.startswith("env:"):
        raise ValueError(f"bolt_auth_ref must start with 'env:', got {ref!r}")
    name = ref[len("env:"):]
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"env var {name!r} (bolt_auth_ref) is not set or empty")
    return value
