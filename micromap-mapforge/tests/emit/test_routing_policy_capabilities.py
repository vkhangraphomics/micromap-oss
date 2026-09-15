"""#202: routing-policy capabilities are an open `collection.collection` (or
`search`) pattern instead of a closed biomedical enum, so a new domain's
capability validates without editing routing_policy.schema.json."""

import pytest

from micromap_mapforge.emit.routing_policy import (
    validate_routing_policy,
    RoutingPolicyValidationError,
)


def _policy(capabilities):
    """A minimal valid routing policy whose ONLY variable is the federation
    destination's `capabilities` list (everything else satisfies the schema)."""
    return {
        "rules": [],
        "default": {"destination": "registry-only"},
        "destinations": {
            "new-federated-instance": {
                "federation": {
                    "source_id": "acme",
                    "display_name": "Acme",
                    "base_url": "https://acme.example.com",
                    "bolt_uri": "bolt://acme:7687",
                    "bolt_auth_ref": "env:ACME_PW",
                    "auth_ref": "env:ACME_TOKEN",
                    "capabilities": capabilities,
                    "organization_id": "acme",
                }
            }
        },
    }


def test_new_domain_capability_validates():
    validate_routing_policy(_policy(["compound.target"]))  # must not raise


def test_existing_capabilities_still_validate():
    validate_routing_policy(_policy(["diseases.metabolites", "taxa.diseases", "search"]))


def test_malformed_capability_rejected():
    with pytest.raises(RoutingPolicyValidationError):
        validate_routing_policy(_policy(["Diseases.Taxa"]))  # uppercase -> invalid
    with pytest.raises(RoutingPolicyValidationError):
        validate_routing_policy(_policy(["foo"]))  # not search, not collection.collection
