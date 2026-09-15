import json
import pytest

from api.federation_tenants import (
    FederationTarget, UnknownTenantError, resolve_bolt_password,
    resolve_federation_target, resolve_federation_target_by_org,
)

_TENANTS = {
    "key_acme": {
        "organization_id": "acme",
        "database": "acme_db",
        "bolt_uri": "bolt://neo4j:7687",
        "bolt_auth_ref": "env:ACME_BOLT_PW",
    }
}


def test_resolve_known_tenant(monkeypatch):
    monkeypatch.setenv("FEDERATION_TENANTS", json.dumps(_TENANTS))
    t = resolve_federation_target("key_acme")
    assert isinstance(t, FederationTarget)
    assert t.organization_id == "acme"
    assert t.database == "acme_db"
    assert t.bolt_uri == "bolt://neo4j:7687"
    assert t.bolt_user == "mapforge"  # default


def test_unknown_tenant_raises(monkeypatch):
    monkeypatch.setenv("FEDERATION_TENANTS", json.dumps(_TENANTS))
    with pytest.raises(UnknownTenantError):
        resolve_federation_target("key_nope")


def test_unset_env_raises(monkeypatch):
    monkeypatch.delenv("FEDERATION_TENANTS", raising=False)
    with pytest.raises(UnknownTenantError):
        resolve_federation_target("key_acme")


def test_resolve_bolt_password(monkeypatch):
    monkeypatch.setenv("ACME_BOLT_PW", "s3cr3t")
    assert resolve_bolt_password("env:ACME_BOLT_PW") == "s3cr3t"


def test_resolve_bolt_password_bad_ref(monkeypatch):
    with pytest.raises(ValueError):
        resolve_bolt_password("literal:nope")


def test_non_dict_config_raises_valueerror(monkeypatch):
    import json as _json
    monkeypatch.setenv("FEDERATION_TENANTS", _json.dumps({"key_x": "not-a-dict"}))
    with pytest.raises(ValueError):
        resolve_federation_target("key_x")


def test_resolve_by_org_known(monkeypatch):
    monkeypatch.setenv("FEDERATION_TENANTS", json.dumps(_TENANTS))
    t = resolve_federation_target_by_org("acme")
    assert isinstance(t, FederationTarget)
    assert t.organization_id == "acme"
    assert t.database == "acme_db"
    assert t.bolt_uri == "bolt://neo4j:7687"


def test_resolve_by_org_unknown(monkeypatch):
    monkeypatch.setenv("FEDERATION_TENANTS", json.dumps(_TENANTS))
    with pytest.raises(UnknownTenantError):
        resolve_federation_target_by_org("ghost")


def test_resolve_by_org_unset_env(monkeypatch):
    monkeypatch.delenv("FEDERATION_TENANTS", raising=False)
    with pytest.raises(UnknownTenantError):
        resolve_federation_target_by_org("acme")
