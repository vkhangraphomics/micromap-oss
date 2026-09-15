from pathlib import Path

import pytest

from micromap_mapforge.emit.routing_policy import (
    RoutingPolicyValidationError,
    load_routing_policy,
    validate_routing_policy,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_valid_policy_parses():
    policy = load_routing_policy(FIXTURES / "valid_routing_policy.yaml")
    assert policy["rules"][0]["destination"] == "micromap-core"
    assert policy["default"]["destination"] == "registry-only"


def test_invalid_tier_rejected():
    with pytest.raises(RoutingPolicyValidationError):
        load_routing_policy(FIXTURES / "invalid_routing_policy.yaml")


def test_missing_default_rejected():
    with pytest.raises(RoutingPolicyValidationError):
        validate_routing_policy({"rules": []})


def test_unknown_destination_rejected():
    with pytest.raises(RoutingPolicyValidationError):
        validate_routing_policy({
            "rules": [],
            "default": {"destination": "quantum-cloud"},
        })


def test_valid_destinations_block_parses():
    policy = load_routing_policy(FIXTURES / "valid_routing_policy_with_destinations.yaml")
    fed = policy["destinations"]["new-federated-instance"]["federation"]
    assert fed["source_id"] == "acme-pharma"
    assert fed["bolt_uri"].startswith("bolt+s://")
    assert fed["capabilities"] == ["diseases.taxa", "taxa.diseases"]


def test_destinations_block_with_missing_required_field_rejected():
    with pytest.raises(RoutingPolicyValidationError):
        validate_routing_policy({
            "rules": [{"match": {"tier": "partner"}, "destination": "new-federated-instance"}],
            "default": {"destination": "registry-only"},
            "destinations": {
                "new-federated-instance": {
                    "federation": {
                        "source_id": "acme-pharma",
                        # missing display_name, base_url, bolt_uri, ...
                    }
                }
            },
        })


def test_destinations_block_rejects_malformed_capability():
    # #202 opened the capabilities set: any well-formed `collection.collection`
    # (or `search`) now validates — a new domain's `unknown.route` is allowed by
    # design. Only MALFORMED capabilities (uppercase / single-token) are rejected.
    with pytest.raises(RoutingPolicyValidationError):
        validate_routing_policy({
            "rules": [],
            "default": {"destination": "registry-only"},
            "destinations": {
                "new-federated-instance": {
                    "federation": {
                        "source_id": "acme-pharma",
                        "display_name": "Acme",
                        "base_url": "https://micromap.acme.example.com",
                        "bolt_uri": "bolt+s://acme.example.com:7687",
                        "bolt_auth_ref": "env:ACME_BOLT_PASSWORD",
                        "auth_ref": "env:ACME_FEDERATION_TOKEN",
                        "capabilities": ["Unknown.Route"],  # uppercase -> malformed
                        "organization_id": "acme",
                    }
                }
            },
        })


def test_destinations_block_rejects_bad_secret_ref():
    with pytest.raises(RoutingPolicyValidationError):
        validate_routing_policy({
            "rules": [],
            "default": {"destination": "registry-only"},
            "destinations": {
                "new-federated-instance": {
                    "federation": {
                        "source_id": "acme-pharma",
                        "display_name": "Acme",
                        "base_url": "https://micromap.acme.example.com",
                        "bolt_uri": "bolt+s://acme.example.com:7687",
                        "bolt_auth_ref": "ACME_BOLT_PASSWORD",  # missing env: prefix
                        "auth_ref": "env:ACME_FEDERATION_TOKEN",
                        "capabilities": ["diseases.taxa"],
                        "organization_id": "acme",
                    }
                }
            },
        })


def test_policy_without_destinations_block_still_valid():
    # destinations is optional; existing policies must not break.
    policy = load_routing_policy(FIXTURES / "valid_routing_policy.yaml")
    assert "destinations" not in policy or policy["destinations"] == {}


# --- #63: provenance opt-out flag ---

def _base_policy() -> dict:
    """Minimal valid policy, used as a baseline for provenance tests."""
    return {
        "rules": [],
        "default": {"destination": "micromap-core"},
    }


def test_schema_provenance_enabled_false_valid():
    policy = _base_policy() | {"provenance": {"enabled": False}}
    validate_routing_policy(policy)   # must not raise


def test_schema_provenance_enabled_true_valid():
    policy = _base_policy() | {"provenance": {"enabled": True}}
    validate_routing_policy(policy)   # must not raise


def test_schema_provenance_absent_valid():
    """Omitting the provenance block is valid (default = enabled at submit time)."""
    validate_routing_policy(_base_policy())   # must not raise


def test_schema_provenance_empty_block_invalid():
    """`provenance: {}` is rejected — `enabled` is required if block is present.

    Prevents `{}` silently meaning enabled via downstream default lookup.
    """
    with pytest.raises(RoutingPolicyValidationError):
        validate_routing_policy(_base_policy() | {"provenance": {}})


def test_schema_provenance_non_bool_invalid():
    with pytest.raises(RoutingPolicyValidationError):
        validate_routing_policy(_base_policy() | {"provenance": {"enabled": "no"}})


def test_schema_provenance_additional_props_invalid():
    with pytest.raises(RoutingPolicyValidationError):
        validate_routing_policy(
            _base_policy() | {"provenance": {"enabled": False, "extra": 1}}
        )
