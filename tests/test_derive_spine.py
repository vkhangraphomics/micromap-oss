import os
import pytest
from database.derive_spine import (
    derive,
    PATHWAY_DISEASE_CYPHER,
    CLEANUP_IMPLICATED_IN_CYPHER,
    GENES_CYPHER,
    DEFAULT_HUB_PATHWAY_MAX,
    DEFAULT_MIN_SUPPORT,
)

DB = os.environ.get("NEO4J_DATABASE", "neo4j")

SEED = """
MERGE (c1:Compound {compound_id:'dvt_c1'})
MERGE (c2:Compound {compound_id:'dvt_c2'})
MERGE (c3:Compound {compound_id:'dvt_c3'})
MERGE (p1:Pathway {pathway_id:'dvt_p1'})
MERGE (d1:Disease {name_normalized:'dvt disease'}) SET d1.name = 'DVT Disease'
MERGE (pr1:Protein {protein_id:'dvt_pr1'}) SET pr1.gene_name = 'DVTGENE', pr1.organization_id = 'dvtorg'
MERGE (c1)-[:PARTICIPATES_IN]->(p1)
MERGE (c2)-[:PARTICIPATES_IN]->(p1)
MERGE (c3)-[:PARTICIPATES_IN]->(p1)
MERGE (c1)-[:LINKED_TO_DISEASE]->(d1)
MERGE (c2)-[:LINKED_TO_DISEASE]->(d1)
"""

CLEANUP = """
MATCH (n) WHERE n.compound_id IN ['dvt_c1','dvt_c2','dvt_c3']
   OR n.pathway_id = 'dvt_p1' OR n.name_normalized = 'dvt disease'
   OR n.protein_id = 'dvt_pr1' OR (n:Gene AND n.name = 'DVTGENE')
DETACH DELETE n
"""

@pytest.fixture
def seeded(neo4j_driver):
    with neo4j_driver.session(database=DB) as s:
        s.run(CLEANUP); s.run(SEED)
    yield neo4j_driver
    with neo4j_driver.session(database=DB) as s:
        s.run(CLEANUP)

def _one(driver, cypher, **params):
    with driver.session(database=DB) as s:
        return s.run(cypher, params).single()

def test_derive_creates_pathway_disease_edge_with_support(seeded):
    # c1 and c2 are BOTH in p1 and linked to d1 -> support 2; c3 is in p1 but
    # not linked to d1 -> excluded. So p1 has exactly one IMPLICATED_IN, to d1.
    derive(seeded, database=DB)
    row = _one(seeded,
        "MATCH (:Pathway {pathway_id:'dvt_p1'})-[r:IMPLICATED_IN]->(:Disease {name_normalized:'dvt disease'}) "
        "RETURN count(r) AS n, min(r.support) AS support, min(r.source) AS source")
    assert row["n"] == 1 and row["support"] == 2 and row["source"] == "derived"
    # p1 is implicated in exactly ONE disease (no spurious edges)
    total = _one(seeded, "MATCH (:Pathway {pathway_id:'dvt_p1'})-[r:IMPLICATED_IN]->() RETURN count(r) AS n")
    assert total["n"] == 1

def test_derive_creates_gene_and_encoded_by(seeded):
    derive(seeded, database=DB)
    row = _one(seeded,
        "MATCH (:Protein {protein_id:'dvt_pr1'})-[:ENCODED_BY]->(g:Gene {name:'DVTGENE'}) "
        "RETURN g.symbol AS symbol, count(g) AS n")
    assert row["n"] == 1 and row["symbol"] == "DVTGENE"

def test_derived_gene_inherits_org_and_encoded_by_has_source(seeded):
    derive(seeded, database=DB)
    row = _one(seeded,
        "MATCH (:Protein {protein_id:'dvt_pr1'})-[e:ENCODED_BY]->(g:Gene {name:'DVTGENE'}) "
        "RETURN g.organization_id AS org, e.source AS src")
    assert row["org"] == "dvtorg"                 # C1: inherited, not null
    assert row["src"] == "derived-from-protein"   # I1: ENCODED_BY stamped

def test_derive_is_idempotent(seeded):
    derive(seeded, database=DB)
    derive(seeded, database=DB)
    edge = _one(seeded,
        "MATCH (:Pathway {pathway_id:'dvt_p1'})-[r:IMPLICATED_IN]->(:Disease {name_normalized:'dvt disease'}) "
        "RETURN count(r) AS n, min(r.support) AS support")
    assert edge["n"] == 1 and edge["support"] == 2   # no duplicate edge, support stable
    enc = _one(seeded,
        "MATCH (:Protein {protein_id:'dvt_pr1'})-[r:ENCODED_BY]->(:Gene {name:'DVTGENE'}) RETURN count(r) AS n")
    assert enc["n"] == 1                              # no duplicate ENCODED_BY

def test_cypher_shape_excludes_hubs_and_floors_support():
    # #274: derivation MERGEs qualifying edges, excludes hub compounds by a
    # degree cutoff, floors support, and stamps the params on the edge for
    # reproducibility. The MERGE statement itself never DELETEs — that is the
    # separate cleanup pass.
    assert "MERGE (p)-[r:IMPLICATED_IN]->(d)" in PATHWAY_DISEASE_CYPHER
    assert "COUNT { (c)-[:PARTICIPATES_IN]->(:Pathway) } <= $hub_max" in PATHWAY_DISEASE_CYPHER
    assert "WHERE support >= $min_support" in PATHWAY_DISEASE_CYPHER
    assert "r.hub_pathway_max = $hub_max" in PATHWAY_DISEASE_CYPHER
    assert "r.min_support = $min_support" in PATHWAY_DISEASE_CYPHER
    assert "DELETE" not in PATHWAY_DISEASE_CYPHER
    assert "MERGE (pr)-[e:ENCODED_BY]->(g)" in GENES_CYPHER
    assert "MERGE (g:Gene {name: pr.gene_name})" in GENES_CYPHER


def test_cleanup_deletes_edges_below_the_floor():
    # #274: the cleanup recomputes non-hub support per existing edge and DELETEs
    # those under the floor — the previously-materialised noise the MERGE alone
    # can't remove.
    assert "DELETE r" in CLEANUP_IMPLICATED_IN_CYPHER
    assert "nonhub_support < $min_support" in CLEANUP_IMPLICATED_IN_CYPHER
    assert "COUNT { (c)-[:PARTICIPATES_IN]->(:Pathway) } <= $hub_max" in CLEANUP_IMPLICATED_IN_CYPHER


def test_defaults_are_the_decided_kgdev_values():
    assert DEFAULT_HUB_PATHWAY_MAX == 40
    assert DEFAULT_MIN_SUPPORT == 2


# --- behavioral (seeded; run in CI / against a live Neo4j) ----------------

HUB_SEED = """
MERGE (c1:Compound {compound_id:'dvt_c1'})
MERGE (c2:Compound {compound_id:'dvt_c2'})
MERGE (cHub:Compound {compound_id:'dvt_hub'})
MERGE (p1:Pathway {pathway_id:'dvt_p1'})
MERGE (p2:Pathway {pathway_id:'dvt_p2'})
MERGE (d1:Disease {name_normalized:'dvt disease'}) SET d1.name = 'DVT Disease'
MERGE (c1)-[:PARTICIPATES_IN]->(p1)
MERGE (c2)-[:PARTICIPATES_IN]->(p1)
MERGE (cHub)-[:PARTICIPATES_IN]->(p1)
MERGE (cHub)-[:PARTICIPATES_IN]->(p2)
MERGE (c1)-[:LINKED_TO_DISEASE]->(d1)
MERGE (c2)-[:LINKED_TO_DISEASE]->(d1)
MERGE (cHub)-[:LINKED_TO_DISEASE]->(d1)
"""

HUB_CLEANUP = """
MATCH (n) WHERE n.compound_id IN ['dvt_c1','dvt_c2','dvt_hub']
   OR n.pathway_id IN ['dvt_p1','dvt_p2'] OR n.name_normalized = 'dvt disease'
DETACH DELETE n
"""


@pytest.fixture
def hub_seeded(neo4j_driver):
    with neo4j_driver.session(database=DB) as s:
        s.run(HUB_CLEANUP); s.run(HUB_SEED)
    yield neo4j_driver
    with neo4j_driver.session(database=DB) as s:
        s.run(HUB_CLEANUP)


def test_hub_compound_is_excluded_from_support(hub_seeded):
    # hub_max=1 → cHub (in 2 pathways) is a hub and must not count. (p1,d1)
    # non-hub support = c1,c2 = 2 (NOT 3), so with min_support=2 the edge exists
    # with support 2.
    derive(hub_seeded, database=DB, hub_pathway_max=1, min_support=2)
    row = _one(hub_seeded,
        "MATCH (:Pathway {pathway_id:'dvt_p1'})-[r:IMPLICATED_IN]->(:Disease {name_normalized:'dvt disease'}) "
        "RETURN count(r) AS n, min(r.support) AS support")
    assert row["n"] == 1 and row["support"] == 2   # cHub excluded, not counted


def test_hub_only_pair_gets_no_edge(hub_seeded):
    # (p2,d1) shares only cHub, which is a hub at hub_max=1 → non-hub support 0
    # → no edge.
    derive(hub_seeded, database=DB, hub_pathway_max=1, min_support=2)
    row = _one(hub_seeded,
        "MATCH (:Pathway {pathway_id:'dvt_p2'})-[r:IMPLICATED_IN]->(:Disease {name_normalized:'dvt disease'}) "
        "RETURN count(r) AS n")
    assert row["n"] == 0


def test_single_support_pair_is_floored_out(hub_seeded):
    # With hub_max large (cHub NOT a hub) but min_support=3, (p1,d1) support = 3
    # (c1,c2,cHub) → edge; raise floor to 4 → no edge.
    derive(hub_seeded, database=DB, hub_pathway_max=40, min_support=4)
    row = _one(hub_seeded,
        "MATCH (:Pathway {pathway_id:'dvt_p1'})-[r:IMPLICATED_IN]->(:Disease {name_normalized:'dvt disease'}) "
        "RETURN count(r) AS n")
    assert row["n"] == 0


def test_cleanup_removes_preexisting_subthreshold_edge(hub_seeded):
    # Pre-materialise a noise edge (p2,d1) as the old additive derivation would
    # have. A derive with hub_max=1/min_support=2 leaves (p2,d1) with 0 non-hub
    # support, so the cleanup must DELETE it.
    with hub_seeded.session(database=DB) as s:
        s.run("MATCH (p:Pathway {pathway_id:'dvt_p2'}), (d:Disease {name_normalized:'dvt disease'}) "
              "MERGE (p)-[r:IMPLICATED_IN]->(d) SET r.support = 1")
    derive(hub_seeded, database=DB, hub_pathway_max=1, min_support=2)
    row = _one(hub_seeded,
        "MATCH (:Pathway {pathway_id:'dvt_p2'})-[r:IMPLICATED_IN]->(:Disease {name_normalized:'dvt disease'}) "
        "RETURN count(r) AS n")
    assert row["n"] == 0   # stale noise edge deleted
