import pytest

from micromap_mapforge.emit.routing import plan_route


def test_plan_route_defaults_to_core():
    mapping = {"source": {"name": "s", "format": "tsv", "path": "s.tsv"}, "entities": [], "relationships": []}
    routing = plan_route(mapping, organization_id="org-xyz")
    assert routing["destination"] == "micromap-core"
    assert routing["organization_id"] == "org-xyz"


def test_plan_route_provenance():
    mapping = {"source": {"name": "s", "format": "tsv", "path": "s.tsv"}, "entities": [], "relationships": []}
    routing = plan_route(mapping, organization_id="partner-xyz")
    assert routing["provenance"]["contributor"] == "partner-xyz"
    assert "submitted_at" in routing["provenance"]
    # mapping_version is a sha256 of the normalized mapping
    assert routing["provenance"]["mapping_version"].startswith("sha256:")


def test_plan_route_explicit_override():
    mapping = {"source": {"name": "s", "format": "tsv", "path": "s.tsv"}, "entities": [], "relationships": []}
    routing = plan_route(mapping, organization_id="o", destination_override="registry-only")
    assert routing["destination"] == "registry-only"


def test_plan_route_embeds_federation_block_when_destination_is_new_federated_instance():
    policy = {
        "rules": [
            {"match": {"tier": "partner", "min_rows": 100000}, "destination": "new-federated-instance"}
        ],
        "default": {"destination": "registry-only"},
        "destinations": {
            "new-federated-instance": {
                "federation": {
                    "source_id": "acme-pharma",
                    "display_name": "Acme Pharma",
                    "base_url": "https://micromap.acme.example.com",
                    "bolt_uri": "bolt+s://acme.example.com:7687",
                    "bolt_auth_ref": "env:ACME_BOLT_PASSWORD",
                    "auth_ref": "env:ACME_FEDERATION_TOKEN",
                    "capabilities": ["diseases.taxa"],
                    "organization_id": "acme",
                },
            }
        },
    }
    routing = plan_route(
        mapping={"taxa": {}},
        organization_id="acme",
        policy=policy,
        contributor={"tier": "partner"},
        row_count=250000,
    )
    assert routing["destination"] == "new-federated-instance"
    fed = routing["destinations"]["new-federated-instance"]["federation"]
    assert fed["source_id"] == "acme-pharma"
    assert fed["bolt_uri"] == "bolt+s://acme.example.com:7687"


def test_plan_route_does_not_emit_destinations_when_routing_to_local():
    policy = {
        "rules": [{"match": {"tier": "internal"}, "destination": "micromap-core"}],
        "default": {"destination": "registry-only"},
    }
    routing = plan_route(
        mapping={}, organization_id="o",
        policy=policy, contributor={"tier": "internal"}, row_count=10,
    )
    assert routing["destination"] == "micromap-core"
    assert "destinations" not in routing


def test_plan_route_raises_when_new_federated_instance_destination_lacks_block():
    policy = {
        "rules": [{"match": {"tier": "partner"}, "destination": "new-federated-instance"}],
        "default": {"destination": "registry-only"},
        # destinations block missing entirely
    }
    with pytest.raises(ValueError, match="destinations.new-federated-instance.federation"):
        plan_route(
            mapping={}, organization_id="o",
            policy=policy, contributor={"tier": "partner"}, row_count=1,
        )


def test_plan_route_no_longer_emits_federation_endpoint_placeholder():
    routing = plan_route(mapping={}, organization_id="o")
    assert "federation_endpoint" not in routing


# --- #63: propagate provenance.enabled from policy to bundle ---

def test_plan_route_propagates_provenance_enabled_false():
    mapping = {"source": {"name": "s", "format": "tsv", "path": "x.tsv"},
               "entities": [], "relationships": []}
    policy = {
        "rules": [],
        "default": {"destination": "micromap-core"},
        "provenance": {"enabled": False},
    }
    routing = plan_route(mapping, "org-xyz", policy=policy)
    assert routing["provenance"]["enabled"] is False
    # Existing keys remain present
    assert "contributor" in routing["provenance"]
    assert "submitted_at" in routing["provenance"]
    assert "mapping_version" in routing["provenance"]


def test_plan_route_propagates_provenance_enabled_true():
    mapping = {"source": {"name": "s", "format": "tsv", "path": "x.tsv"},
               "entities": [], "relationships": []}
    policy = {
        "rules": [],
        "default": {"destination": "micromap-core"},
        "provenance": {"enabled": True},
    }
    routing = plan_route(mapping, "org-xyz", policy=policy)
    assert routing["provenance"]["enabled"] is True


def test_plan_route_omits_provenance_enabled_when_policy_lacks_block():
    """Policy without a provenance block → bundle's provenance has no 'enabled' key.

    Submit-side defensive default supplies True. This keeps existing bundles
    bit-for-bit identical to today.
    """
    mapping = {"source": {"name": "s", "format": "tsv", "path": "x.tsv"},
               "entities": [], "relationships": []}
    policy = {
        "rules": [],
        "default": {"destination": "micromap-core"},
    }
    routing = plan_route(mapping, "org-xyz", policy=policy)
    assert "enabled" not in routing["provenance"]


def test_plan_route_omits_provenance_enabled_when_no_policy():
    """No policy at all (M2 fallback path) → bundle's provenance has no 'enabled' key."""
    mapping = {"source": {"name": "s", "format": "tsv", "path": "x.tsv"},
               "entities": [], "relationships": []}
    routing = plan_route(mapping, "org-xyz")   # no policy=
    assert "enabled" not in routing["provenance"]
