"""Cross-tenant read isolation (#200): a caller sees their org + shared, never
another tenant's private data. Integration tests require live Neo4j; skipped
otherwise (mirrors tests/conftest.py::neo4j_driver). Plus a unit assertion that
the taxa list query carries the org scope."""
import asyncio
from unittest.mock import MagicMock, patch

import pytest

from api.scoping import OrgScope

ORG_A = "test_org_a"
ORG_B = "test_org_b"
SHARED = "default"


@pytest.fixture
def seeded_graph(neo4j_driver):
    db = getattr(neo4j_driver, "_test_database", "neo4j")
    with neo4j_driver.session(database=db) as s:
        s.run(
            """
            MERGE (a:Taxon {taxon_id: 'ISOTEST:A'})      SET a.name='IsoTest A', a.organization_id=$a
            MERGE (b:Taxon {taxon_id: 'ISOTEST:B'})      SET b.name='IsoTest B', b.organization_id=$b
            MERGE (sh:Taxon {taxon_id: 'ISOTEST:SHARED'}) SET sh.name='IsoTest Shared', sh.organization_id=$shared
            """,
            {"a": ORG_A, "b": ORG_B, "shared": SHARED},
        )
    yield
    with neo4j_driver.session(database=db) as s:
        s.run("MATCH (t:Taxon) WHERE t.taxon_id STARTS WITH 'ISOTEST:' DETACH DELETE t")


_SCOPED_QUERY = """
MATCH (t:Taxon)
WHERE t.taxon_id STARTS WITH 'ISOTEST:'
  AND (t.organization_id = $organization_id OR t.organization_id IN $public_orgs)
RETURN t.taxon_id AS id ORDER BY id
"""


def _scoped_ids(neo4j_driver, org):
    """Run the shared+private predicate as the data layer would, via the fixture
    session (MicrobiomeKG opens its own driver from env, so we reuse the test
    driver directly instead)."""
    db = getattr(neo4j_driver, "_test_database", "neo4j")
    with neo4j_driver.session(database=db) as s:
        result = s.run(_SCOPED_QUERY, {"organization_id": org, "public_orgs": [SHARED]})
        return [r["id"] for r in result]


@pytest.mark.integration
def test_caller_a_sees_own_and_shared_not_b(neo4j_driver, seeded_graph):
    assert _scoped_ids(neo4j_driver, ORG_A) == ["ISOTEST:A", "ISOTEST:SHARED"]


@pytest.mark.integration
def test_caller_b_cannot_see_a(neo4j_driver, seeded_graph):
    assert _scoped_ids(neo4j_driver, ORG_B) == ["ISOTEST:B", "ISOTEST:SHARED"]


def test_taxa_list_query_is_org_scoped():
    """Unit: the taxa list endpoint must stamp the caller's org onto every query
    it runs (count + page), regardless of optional filters."""
    from api.routes import taxa as taxa_mod

    fake = MagicMock()
    fake.execute_cypher = MagicMock(return_value=[])
    with patch.object(taxa_mod, "get_kg", return_value=fake):
        asyncio.run(
            taxa_mod.list_taxa(
                rank=None,
                kingdom=None,
                search=None,
                limit=100,
                offset=0,
                scope=OrgScope("acme", ["default"]),
            )
        )

    assert fake.execute_cypher.call_args_list, "no query executed"
    for call in fake.execute_cypher.call_args_list:
        query, params = call.args
        assert "organization_id" in query
        assert params["organization_id"] == "acme"
        assert params["public_orgs"] == ["default"]


# --- #297: contributed-edge (ASSOCIATED_WITH_DISEASE) org scoping ----------

def test_disease_taxa_query_scopes_the_edge_not_just_nodes():
    """Unit: `/diseases/{id}/taxa` must scope the ASSOCIATED_WITH_DISEASE edge,
    so an org-stamped edge between two shared (`default`) nodes cannot leak to
    every caller (the #284 leak in edge form)."""
    from unittest.mock import MagicMock as MM

    from api.routes import diseases as dz

    def _exec(query, params=None):
        if "AS total" in query:
            return [{"total": 0}]                 # count query
        if "ASSOCIATED_WITH_DISEASE" in query:
            return []                             # main taxa query
        return [{"exists": True}]                 # disease_check passes

    fake = MagicMock()
    fake.execute_cypher = MagicMock(side_effect=_exec)
    with patch.object(dz, "get_kg", return_value=fake):
        asyncio.run(dz.get_disease_taxa(
            disease_id="parkinson-disease", request=MM(), limit=100, offset=0,
            direction=None, rank=None, evidence_level=None, sort_by=None,
            scope=OrgScope("acme", ["default"]),
        ))

    edge_scoped = [c.args[0] for c in fake.execute_cypher.call_args_list
                   if "ASSOCIATED_WITH_DISEASE" in c.args[0]]
    assert edge_scoped, "no edge-traversing query ran"
    for q in edge_scoped:
        assert "r.organization_id" in q, "AWD edge not org-scoped"


_EDGE_SEED = """
MERGE (d:Disease {name_normalized: 'isoedge-disease'})  SET d.name='IsoEdge', d.organization_id=$shared
MERGE (ta:Taxon {taxon_id: 'ISOEDGE:A'}) SET ta.name='A', ta.organization_id=$shared
MERGE (tb:Taxon {taxon_id: 'ISOEDGE:B'}) SET tb.name='B', tb.organization_id=$shared
MERGE (tn:Taxon {taxon_id: 'ISOEDGE:N'}) SET tn.name='N', tn.organization_id=$shared
MERGE (ta)-[ra:ASSOCIATED_WITH_DISEASE]->(d) SET ra.organization_id=$a
MERGE (tb)-[rb:ASSOCIATED_WITH_DISEASE]->(d) SET rb.organization_id=$b
MERGE (tn)-[rn:ASSOCIATED_WITH_DISEASE]->(d) REMOVE rn.organization_id
"""

# All three taxa AND the disease are `default`-org, so NODE scoping passes for
# every caller — the only thing distinguishing the three edges is the edge's own
# organization_id. That isolates edge-scoping: this is exactly the #284 shape.
_EDGE_READ = """
MATCH (t:Taxon)-[r:ASSOCIATED_WITH_DISEASE]->(d:Disease {name_normalized: 'isoedge-disease'})
WHERE (r.organization_id IS NULL
       OR r.organization_id = $organization_id
       OR r.organization_id IN $public_orgs)
RETURN t.taxon_id AS id ORDER BY id
"""


@pytest.fixture
def seeded_edges(neo4j_driver):
    db = getattr(neo4j_driver, "_test_database", "neo4j")
    with neo4j_driver.session(database=db) as s:
        s.run(_EDGE_SEED, {"a": ORG_A, "b": ORG_B, "shared": SHARED})
    yield
    with neo4j_driver.session(database=db) as s:
        s.run("MATCH (t:Taxon) WHERE t.taxon_id STARTS WITH 'ISOEDGE:' DETACH DELETE t")
        s.run("MATCH (d:Disease {name_normalized: 'isoedge-disease'}) DETACH DELETE d")


def _scoped_edge_ids(neo4j_driver, org):
    db = getattr(neo4j_driver, "_test_database", "neo4j")
    with neo4j_driver.session(database=db) as s:
        return [r["id"] for r in s.run(
            _EDGE_READ, {"organization_id": org, "public_orgs": [SHARED]})]


@pytest.mark.integration
def test_edge_org_scoping_owner_sees_own_and_null(neo4j_driver, seeded_edges):
    # org A sees its own contributed edge + the shared/ingested (null-org) edge
    assert _scoped_edge_ids(neo4j_driver, ORG_A) == ["ISOEDGE:A", "ISOEDGE:N"]


@pytest.mark.integration
def test_edge_org_scoping_does_not_leak_across_orgs(neo4j_driver, seeded_edges):
    # org B never sees A's contributed edge (the #284 leak stays closed) — only
    # its own + the null-org edge
    assert _scoped_edge_ids(neo4j_driver, ORG_B) == ["ISOEDGE:B", "ISOEDGE:N"]


@pytest.mark.integration
def test_edge_org_scoping_default_sees_only_shared_null_edge(neo4j_driver, seeded_edges):
    # a plain reference caller sees ONLY the null-org (ingested) edge — neither
    # customer's org-stamped edge leaks into the shared reference view
    assert _scoped_edge_ids(neo4j_driver, SHARED) == ["ISOEDGE:N"]
