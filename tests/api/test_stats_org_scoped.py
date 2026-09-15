"""Task 6 (#200): /api/v1/stats must carry org-scope params in its Cypher query."""
import asyncio
from unittest.mock import MagicMock, patch

from starlette.requests import Request

from api.scoping import OrgScope

# Every key the endpoint reads off the result row (for total_nodes / total_rels sums).
_ZERO_ROW = {
    "taxa": 0,
    "diseases": 0,
    "metabolites": 0,
    "pathways": 0,
    "drugs": 0,
    "genes": 0,
    "proteins": 0,
    "papers": 0,
    "disease_associations": 0,
    "produces": 0,
    "taxonomy_hierarchy": 0,
    "participates_in": 0,
    "targets": 0,
    "mentioned_in": 0,
    "linked_to_disease": 0,
    "effective_against": 0,
    "belongs_to_class": 0,
    "processes": 0,
    "same_as": 0,
}


def _make_request() -> Request:
    """Minimal Starlette Request satisfying slowapi's isinstance check."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/stats",
        "query_string": b"",
        "headers": [],
    }
    return Request(scope)


def test_stats_query_is_org_scoped():
    """The stats endpoint must pass organization_id and public_orgs to the KG."""
    import api.main as main_mod

    fake = MagicMock()
    fake.execute_cypher = MagicMock(return_value=[_ZERO_ROW])
    with patch.object(main_mod, "kg", fake):
        asyncio.run(
            main_mod.get_kg_stats(
                request=_make_request(),
                scope=OrgScope("acme", ["default"]),
            )
        )

    query, params = fake.execute_cypher.call_args.args
    assert "organization_id" in query, "stats query must reference $organization_id"
    assert params["organization_id"] == "acme"
    assert params["public_orgs"] == ["default"]
