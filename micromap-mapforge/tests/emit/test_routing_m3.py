from micromap_mapforge.emit.routing import plan_route


_EMPTY_MAPPING = {"source": {"name": "s", "format": "tsv", "path": "s.tsv"},
                  "entities": [], "relationships": []}


def _policy():
    return {
        "rules": [
            {"match": {"sensitivity": "pii"}, "destination": "registry-only"},
            {"match": {"tier": "internal"}, "destination": "micromap-core"},
            {"match": {"tier": "partner", "max_rows": 99999}, "destination": "micromap-core"},
            {"match": {"tier": "partner", "min_rows": 100000}, "destination": "new-federated-instance"},
            {"match": {"tier": "external"}, "destination": "registry-only"},
        ],
        "default": {"destination": "registry-only"},
        "destinations": {
            "new-federated-instance": {
                "federation": {
                    "source_id": "test-remote",
                    "display_name": "Test Remote",
                    "base_url": "https://remote.example.com",
                    "bolt_uri": "bolt+s://remote.example.com:7687",
                    "bolt_auth_ref": "env:TEST_REMOTE_BOLT_PASSWORD",
                    "auth_ref": "env:TEST_REMOTE_FEDERATION_TOKEN",
                    "capabilities": ["diseases.taxa"],
                    "organization_id": "test",
                },
            }
        },
    }


def test_internal_routes_to_core():
    r = plan_route(_EMPTY_MAPPING, organization_id="acme",
                   policy=_policy(),
                   contributor={"contributor": "acme", "tier": "internal"})
    assert r["destination"] == "micromap-core"


def test_partner_small_routes_to_core():
    r = plan_route(_EMPTY_MAPPING, organization_id="partner-xyz",
                   policy=_policy(),
                   contributor={"contributor": "partner-xyz", "tier": "partner"},
                   row_count=500)
    assert r["destination"] == "micromap-core"


def test_partner_large_routes_to_new_instance():
    r = plan_route(_EMPTY_MAPPING, organization_id="partner-xyz",
                   policy=_policy(),
                   contributor={"contributor": "partner-xyz", "tier": "partner"},
                   row_count=500000)
    assert r["destination"] == "new-federated-instance"


def test_pii_overrides_tier():
    r = plan_route(_EMPTY_MAPPING, organization_id="acme",
                   policy=_policy(),
                   contributor={"contributor": "acme", "tier": "internal", "sensitivity": "pii"})
    # PII rule appears FIRST in the policy, so it wins over tier=internal.
    assert r["destination"] == "registry-only"


def test_unmatched_falls_back_to_default():
    r = plan_route(_EMPTY_MAPPING, organization_id="mystery",
                   policy={"rules": [], "default": {"destination": "registry-only"}},
                   contributor={"contributor": "mystery", "tier": "external"})
    assert r["destination"] == "registry-only"


def test_explicit_override_still_wins():
    r = plan_route(_EMPTY_MAPPING, organization_id="acme",
                   policy=_policy(),
                   contributor={"contributor": "acme", "tier": "internal"},
                   destination_override="registry-only")
    assert r["destination"] == "registry-only"


def test_no_policy_falls_back_to_core_m2_compat():
    # M2 behavior — no policy, no contributor → "micromap-core"
    r = plan_route(_EMPTY_MAPPING, organization_id="acme")
    assert r["destination"] == "micromap-core"
