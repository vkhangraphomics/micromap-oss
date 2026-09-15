import os
from datetime import datetime
import pytest
from neo4j import GraphDatabase
from micromap_mapforge.provenance.spine import (
    EndpointRef, ExperimentRecord, AnalysisRecord, AssertionRecord, FindingRecord,
    write_finding,
)

DB = os.environ.get("NEO4J_DATABASE", "neo4j")

# Needs a live Neo4j; runs only in the integration job (via testcontainers,
# like the other DB integration tests), skips if Docker/testcontainers is
# unavailable, and is excluded from the unit job. See #288.
pytestmark = pytest.mark.integration

@pytest.fixture(scope="module")
def neo4j_container():
    try:
        from testcontainers.neo4j import Neo4jContainer
    except ImportError:
        pytest.skip("testcontainers[neo4j] not installed")
    try:
        with Neo4jContainer("neo4j:5.15") as n4j:
            yield n4j
    except Exception as e:
        pytest.skip(f"Could not start Neo4j container: {e}")

@pytest.fixture(scope="module")
def driver(neo4j_container):
    d = GraphDatabase.driver(
        neo4j_container.get_connection_url(),
        auth=(neo4j_container.username, neo4j_container.password),
    )
    yield d
    d.close()

@pytest.fixture(autouse=True)
def clean(driver):
    with driver.session(database=DB) as s:
        s.run("MATCH (n) WHERE n:Experiment OR n:Analysis OR n:Assertion DETACH DELETE n")
        s.run("MATCH (n:Taxon {ncbi_tax_id:'209879'}) DETACH DELETE n")
        s.run("MATCH (n:Disease {name_normalized:'parkinson disease'}) DETACH DELETE n")
        s.run("MATCH (d:Decision) WHERE d.id IN ['dec-1','dec-other'] DETACH DELETE d")
        # seed endpoints for the resolved case
        s.run("CREATE (:Taxon {ncbi_tax_id:'209879', name:'Allisonella', organization_id:'org1'})")
        s.run("CREATE (:Disease {name_normalized:'parkinson disease', name:'Parkinson disease', organization_id:'org1'})")
    return

@pytest.fixture(autouse=True)
def _canonical_org1(monkeypatch):
    # The fixtures below assert on the projected reference edge, so the test org
    # must be canonical for projection to run (#284). Non-canonical behavior is
    # covered explicitly by test_non_canonical_org_records_but_does_not_project,
    # which overrides this within its own body.
    monkeypatch.setenv("CANONICAL_ORGS", "org1")


def _finding(conf=0.82, analysis_id="an1", subj="209879", supersedes=None, predicate="ASSOCIATED_WITH_DISEASE",
             subject_ref=None, object_ref=None, org="org1"):
    return FindingRecord(
        experiment=ExperimentRecord(id=f"exp-{org}", organization_id=org, title="PD"),
        analysis=AnalysisRecord(id=analysis_id, organization_id=org,
                                method="agent-reasoning", occurred_at=datetime(2026,6,30)),
        assertions=[AssertionRecord(
            predicate=predicate,
            subject=subject_ref or EndpointRef("Taxon","ncbi_tax_id",subj),
            object_=object_ref or EndpointRef("Disease","name_normalized","parkinson disease"),
            confidence=conf, organization_id=org,
            asserted_at=datetime(2026,6,30), valid_from=datetime(2026,6,30),
            analysis_id=analysis_id, direction="increased",
            supersedes=supersedes or [],
        )],
    )

def test_write_resolves_and_projects(driver):
    res = write_finding(driver, _finding(), database=DB)
    assert res["resolved_count"] == 1 and res["unresolved_count"] == 0
    assert res["assertions"][0]["resolved"] is True
    assert res["assertions"][0]["projected"] is True
    with driver.session(database=DB) as s:
        row = s.run(
            "MATCH (:Taxon {ncbi_tax_id:'209879'})-[r:ASSOCIATED_WITH_DISEASE]->"
            "(:Disease {name_normalized:'parkinson disease'}) "
            "RETURN r.confidence AS c, r.assertion_id AS aid, r.source AS src"
        ).single()
    assert row["c"] == 0.82 and row["src"] == "provenance-spine" and row["aid"]

def test_non_canonical_org_projects_parallel_edge(driver, monkeypatch):
    # #297 Part B: a non-canonical (customer/eval) assertion now PROJECTS — but as
    # a PARALLEL edge keyed on its own org, distinct from the shared reference
    # edge. Edge-org-aware reads (#297 Part A) scope it to the owning org, so it
    # never leaks cross-org (the #284 property is preserved by scoping, not by
    # refusing to project).
    monkeypatch.setenv("CANONICAL_ORGS", "default")  # org1 is non-canonical here
    res = write_finding(driver, _finding(org="org1"), database=DB)
    assert res["resolved_count"] == 1
    assert res["assertions"][0]["resolved"] is True
    assert res["assertions"][0]["projected"] is True
    assert res["assertions"][0]["projection_kind"] == "parallel"
    with driver.session(database=DB) as s:
        row = s.run(
            "MATCH (:Taxon {ncbi_tax_id:'209879'})-[r:ASSOCIATED_WITH_DISEASE]->"
            "(:Disease {name_normalized:'parkinson disease'}) "
            "RETURN count(r) AS n, collect(r.organization_id) AS orgs"
        ).single()
        deg = s.run(
            "MATCH (a:Assertion) RETURN size([(a)-[:SUBJECT]->()|1]) AS s, "
            "size([(a)-[:OBJECT]->()|1]) AS o"
        ).single()
    assert row["n"] == 1
    assert row["orgs"] == ["org1"]             # the customer's own org-stamped edge
    assert deg["s"] == 1 and deg["o"] == 1     # lineage still fully recorded


def test_canonical_and_customer_edges_coexist_as_parallel(driver, monkeypatch):
    # #297 Part B: a canonical (default) projection and a customer (acme) projection
    # for the SAME (taxon, disease) yield TWO distinct edges — the customer's
    # contribution sits alongside the reference edge, never replacing it.
    monkeypatch.setenv("CANONICAL_ORGS", "default")
    write_finding(driver, _finding(org="default", analysis_id="an-canon"), database=DB)
    write_finding(driver, _finding(org="acme", analysis_id="an-cust", conf=0.5), database=DB)
    with driver.session(database=DB) as s:
        orgs = sorted(s.run(
            "MATCH (:Taxon {ncbi_tax_id:'209879'})-[r:ASSOCIATED_WITH_DISEASE]->"
            "(:Disease {name_normalized:'parkinson disease'}) "
            "RETURN r.organization_id AS org"
        ).value("org"))
    assert orgs == ["acme", "default"]


def test_canonical_projection_does_not_corrupt_a_pre_existing_customer_edge(driver, monkeypatch):
    # #297 Part B — the reverse-collision guard: if a customer's parallel edge
    # already exists, a LATER canonical projection for the same pair must NOT
    # pattern-match and re-stamp it to `default` (which would leak the customer's
    # edge to every caller). The canonical path selects the edge to update by org,
    # so the acme edge is untouched and a separate default edge is created.
    monkeypatch.setenv("CANONICAL_ORGS", "default")
    write_finding(driver, _finding(org="acme", analysis_id="an-cust", conf=0.5), database=DB)
    write_finding(driver, _finding(org="default", analysis_id="an-canon"), database=DB)
    with driver.session(database=DB) as s:
        rows = {r["org"]: r["conf"] for r in s.run(
            "MATCH (:Taxon {ncbi_tax_id:'209879'})-[r:ASSOCIATED_WITH_DISEASE]->"
            "(:Disease {name_normalized:'parkinson disease'}) "
            "RETURN r.organization_id AS org, r.confidence AS conf")}
    assert set(rows) == {"acme", "default"}    # two parallel edges, acme not relabeled
    assert rows["acme"] == 0.5                  # customer edge intact (not overwritten)


def test_projected_edge_is_org_stamped(driver):
    # #284: a canonical assertion projects, and the concrete edge carries the
    # asserting org so an edge-org-aware read can scope it (a NULL edge-org is
    # what let rehearsal edges masquerade as reference truth).
    res = write_finding(driver, _finding(), database=DB)
    assert res["assertions"][0]["projected"] is True
    with driver.session(database=DB) as s:
        org = s.run(
            "MATCH (:Taxon {ncbi_tax_id:'209879'})-[r:ASSOCIATED_WITH_DISEASE]->"
            "(:Disease {name_normalized:'parkinson disease'}) RETURN r.organization_id AS org"
        ).single()["org"]
    assert org == "org1"


def test_projection_preserves_ingested_edge_source(driver):
    # An edge already loaded from an ingestion source must keep its source +
    # confidence when the spine asserts the same triple; the spine only records
    # the assertion_id linkage (it must not clobber ingested provenance).
    with driver.session(database=DB) as s:
        s.run(
            "MATCH (t:Taxon {ncbi_tax_id:'209879'}), (d:Disease {name_normalized:'parkinson disease'}) "
            "MERGE (t)-[r:ASSOCIATED_WITH_DISEASE]->(d) SET r.source='GMRepo', r.confidence=0.30"
        )
    write_finding(driver, _finding(conf=0.82), database=DB)
    with driver.session(database=DB) as s:
        row = s.run(
            "MATCH (:Taxon {ncbi_tax_id:'209879'})-[r:ASSOCIATED_WITH_DISEASE]->"
            "(:Disease {name_normalized:'parkinson disease'}) "
            "RETURN r.source AS src, r.confidence AS c, r.assertion_id AS aid"
        ).single()
    assert row["src"] == "GMRepo"      # ingested source preserved
    assert row["c"] == 0.30            # ingested confidence preserved
    assert row["aid"]                  # spine linkage still recorded

def test_idempotent_resubmit(driver):
    write_finding(driver, _finding(), database=DB)
    write_finding(driver, _finding(), database=DB)
    with driver.session(database=DB) as s:
        n = s.run("MATCH (a:Assertion) RETURN count(a) AS n").single()["n"]
    assert n == 1

def test_unresolved_endpoint_is_non_gating(driver):
    res = write_finding(driver, _finding(subj="does-not-exist"), database=DB)
    assert res["unresolved_count"] == 1
    assert res["assertions"][0]["resolved"] is False
    assert res["assertions"][0]["projected"] is False

def test_supersede_reprojects_to_new_confidence(driver):
    first = write_finding(driver, _finding(conf=0.50, analysis_id="an1"), database=DB)
    old_id = first["assertions"][0]["id"]
    write_finding(driver, _finding(conf=0.90, analysis_id="an2", supersedes=[old_id]), database=DB)
    with driver.session(database=DB) as s:
        c = s.run(
            "MATCH (:Taxon {ncbi_tax_id:'209879'})-[r:ASSOCIATED_WITH_DISEASE]->() RETURN r.confidence AS c"
        ).single()["c"]
    assert c == 0.90  # projection followed the current (non-superseded) assertion

def test_wired_predicate_with_mismatched_labels_resolves_but_does_not_project(driver):
    # PRODUCES is wired for (Taxon, Compound); here we swap the labels so the
    # assertion's actual subject/object are (Disease, Taxon) instead — labels
    # still resolve (both endpoints exist), but projection must be skipped to
    # avoid materializing a nonsensical (Disease)-[:PRODUCES]->(Taxon) edge.
    rec = _finding(
        analysis_id="an-mismatch",
        predicate="PRODUCES",
        subject_ref=EndpointRef("Disease", "name_normalized", "parkinson disease"),
        object_ref=EndpointRef("Taxon", "ncbi_tax_id", "209879"),
    )
    res = write_finding(driver, rec, database=DB)
    assert res["resolved_count"] == 1 and res["unresolved_count"] == 0
    assert res["assertions"][0]["resolved"] is True
    assert res["assertions"][0]["projected"] is False
    with driver.session(database=DB) as s:
        # Scoped to the mismatched-label endpoints used above (Disease as subject,
        # Taxon as object) — a database-wide count would false-negative on any DB
        # with real data, since PRODUCES (Taxon->Compound) is a core populated
        # relationship elsewhere in the graph.
        n = s.run(
            "MATCH (:Disease {name_normalized:'parkinson disease'})-[r:PRODUCES]->"
            "(:Taxon {ncbi_tax_id:'209879'}) RETURN count(r) AS n"
        ).single()["n"]
    assert n == 0

def _seed_decision(driver, did="dec-1", org="org1"):
    with driver.session(database=DB) as s:
        s.run("MERGE (d:Decision {id:$id}) SET d.organization_id=$org, "
              "d.summary='verdict', d.action_type='hypothesis_verdict', "
              "d.decision_outcome='supported', d.occurred_at='2026-06-30T00:00:00Z'",
              {"id": did, "org": org})

def _recorded_edge_count(driver, aid="an1", did="dec-1"):
    with driver.session(database=DB) as s:
        return s.run("MATCH (:Analysis {id:$aid})-[r:RECORDED]->(:Decision {id:$did}) "
                     "RETURN count(r) AS n", {"aid": aid, "did": did}).single()["n"]

def test_write_finding_links_decision_by_id(driver):
    _seed_decision(driver)
    res = write_finding(driver, _finding(), database=DB, link_decision_ids=["dec-1"])
    assert res["recorded_links"] == 1
    assert _recorded_edge_count(driver) == 1

def test_write_finding_link_is_idempotent(driver):
    _seed_decision(driver)
    write_finding(driver, _finding(), database=DB, link_decision_ids=["dec-1"])
    write_finding(driver, _finding(), database=DB, link_decision_ids=["dec-1"])
    assert _recorded_edge_count(driver) == 1

def test_write_finding_unknown_decision_is_noop(driver):
    res = write_finding(driver, _finding(), database=DB, link_decision_ids=["does-not-exist"])
    assert res["recorded_links"] == 0
    # finding itself still recorded
    assert res["resolved_count"] == 1

def test_write_finding_cross_org_decision_is_noop(driver):
    _seed_decision(driver, did="dec-other", org="other-org")
    res = write_finding(driver, _finding(), database=DB, link_decision_ids=["dec-other"])
    assert res["recorded_links"] == 0
    assert _recorded_edge_count(driver, did="dec-other") == 0

def test_write_finding_no_link_ids_behaves_as_before(driver):
    res = write_finding(driver, _finding(), database=DB)
    assert res["recorded_links"] == 0
    assert res["resolved_count"] == 1 and res["assertions"][0]["projected"] is True
