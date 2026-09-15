"""The data layer must not contain the nullable org bypass (#200)."""
from pathlib import Path


def test_no_nullable_org_bypass_remains():
    src = Path("integrations/neo4j_microbiome.py").read_text(encoding="utf-8")
    assert "organization_id IS NULL" not in src, (
        "Nullable org bypass still present — passing None would return all tenants' data."
    )
    # The shared+private predicate must be present instead.
    assert "organization_id IN $public_orgs" in src
