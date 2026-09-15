import os
from datetime import datetime
import pytest
from fastapi.testclient import TestClient
from neo4j import GraphDatabase
from api.dependencies import resolve_organization_id, verify_api_key
from api.main import app
from micromap_mapforge.provenance.spine import (
    EndpointRef, ExperimentRecord, AnalysisRecord, AssertionRecord, FindingRecord, write_finding,
)

client = TestClient(app)

# Runs against a throwaway Neo4j provisioned by `seeded_neo4j_env` (#307). The
# seed fixture previously opened bolt://localhost:7687 unconditionally and
# errored with no box present -- 11 errors indistinguishable from real
# regressions, and the reason `-m integration` could not run in CI. Every
# assertion here is about data this module seeds itself, so an empty container
# serves it exactly as well as a live box.
#
# NEO4J_* must be read lazily (helpers below, not module constants): the fixture
# sets the environment, so anything resolved at import time would capture the
# pre-container values.
pytestmark = [pytest.mark.integration, pytest.mark.seeded]


def _db():
    return os.environ.get("NEO4J_DATABASE", "neo4j")


def _driver():
    return GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    )


@pytest.fixture(autouse=True)
def seed(seeded_neo4j_env):
    d = _driver()
    with d.session(database=_db()) as s:
        s.run("MATCH (n) WHERE n:Experiment OR n:Analysis OR n:Assertion DETACH DELETE n")
        s.run("MATCH (d:Decision) WHERE d.id IN ['dec-1','dec-2','dec-live','dec-a','dec-b'] DETACH DELETE d")
        s.run("MERGE (:Taxon {ncbi_tax_id:'209879', name:'Allisonella', organization_id:'org1'})")
        s.run("MERGE (:Disease {name_normalized:'parkinson disease', name:'Parkinson disease', organization_id:'org1'})")
    def _f(conf, aid, valid_from, supersedes=None):
        return FindingRecord(
            experiment=ExperimentRecord(id="exp1", organization_id="org1", title="PD"),
            analysis=AnalysisRecord(id=aid, organization_id="org1", method="agent-reasoning",
                                    occurred_at=valid_from),
            assertions=[AssertionRecord(
                predicate="ASSOCIATED_WITH_DISEASE",
                subject=EndpointRef("Taxon","ncbi_tax_id","209879"),
                object_=EndpointRef("Disease","name_normalized","parkinson disease"),
                confidence=conf, organization_id="org1",
                asserted_at=valid_from, valid_from=valid_from, analysis_id=aid,
                supersedes=supersedes or [])])
    r1 = write_finding(d, _f(0.50, "an1", datetime(2026,1,1)), database=_db())
    write_finding(d, _f(0.90, "an2", datetime(2026,6,1),
                        supersedes=[r1["assertions"][0]["id"]]), database=_db())
    d.close()
    app.dependency_overrides[resolve_organization_id] = lambda: "org1"
    app.dependency_overrides[verify_api_key] = lambda: "test-key"
    yield
    app.dependency_overrides.pop(resolve_organization_id, None)
    app.dependency_overrides.pop(verify_api_key, None)

def _link_finding(aid):
    return FindingRecord(
        experiment=ExperimentRecord(id="exp1", organization_id="org1", title="PD"),
        analysis=AnalysisRecord(id=aid, organization_id="org1", method="agent-reasoning",
                                occurred_at=datetime(2026, 6, 1)),
        assertions=[AssertionRecord(
            predicate="ASSOCIATED_WITH_DISEASE",
            subject=EndpointRef("Taxon", "ncbi_tax_id", "209879"),
            object_=EndpointRef("Disease", "name_normalized", "parkinson disease"),
            confidence=0.90, organization_id="org1",
            asserted_at=datetime(2026, 6, 1), valid_from=datetime(2026, 6, 1),
            analysis_id=aid)])

def test_lineage_current_returns_latest(seed):
    r = client.post("/api/v1/provenance/lineage", json={"entity": "parkinson disease"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1
    assert body["paths"][0]["assertion"]["confidence"] == 0.90
    assert body["paths"][0]["subject"] == "Allisonella"
    assert body["paths"][0]["experiment"]["id"] == "exp1"


def test_lineage_current_excludes_superseded(seed):
    # #285 regression: the superseded record (an1, confidence 0.50) must NOT
    # appear in a default (current-truth) read — only the superseding an2 (0.90).
    r = client.post("/api/v1/provenance/lineage", json={"entity": "parkinson disease"})
    assert r.status_code == 200
    confidences = [p["assertion"]["confidence"] for p in r.json()["paths"]]
    assert 0.50 not in confidences   # the superseded record must not appear
    assert 0.90 in confidences       # only the superseding record is current

def test_lineage_as_of_returns_historical(seed):
    r = client.post("/api/v1/provenance/lineage",
                    json={"entity": "parkinson disease", "as_of": "2026-03-01T00:00:00"})
    assert r.status_code == 200
    assert r.json()["paths"][0]["assertion"]["confidence"] == 0.50

def test_lineage_matches_taxon_subject(seed):
    r = client.post("/api/v1/provenance/lineage", json={"entity": "Allisonella"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1
    assert any(p["subject"] == "Allisonella" for p in body["paths"])

def test_lineage_is_org_scoped(seed):
    app.dependency_overrides[resolve_organization_id] = lambda: "other-org"
    try:
        r = client.post("/api/v1/provenance/lineage", json={"entity": "parkinson disease"})
        assert r.status_code == 200
        assert r.json()["count"] == 0
    finally:
        app.dependency_overrides[resolve_organization_id] = lambda: "org1"


def test_lineage_requires_api_key(seed, monkeypatch):
    # Exercise the real verify_api_key (the endpoint must reject a keyless call
    # like its sibling provenance routes) — drop the fixture's auth override and
    # ensure at least one key is configured so dev-mode (empty keys) is off.
    monkeypatch.setenv("API_KEYS", "real-key-123")
    app.dependency_overrides.pop(verify_api_key, None)
    r = client.post("/api/v1/provenance/lineage", json={"entity": "parkinson disease"})
    assert r.status_code == 401


def _link_decision(did="dec-1", aid="an2"):
    """Seed a :Decision and link it from an existing analysis via the REST ingest."""
    # create the decision node directly (org1), then link from the finding side
    d = _driver()
    with d.session(database=_db()) as s:
        s.run("MATCH (x:Decision {id:$id}) DETACH DELETE x", {"id": did})
        s.run("MERGE (x:Decision {id:$id}) SET x.organization_id='org1', x.summary='verdict', "
              "x.action_type='hypothesis_verdict', x.decision_outcome='supported', "
              "x.occurred_at='2026-06-30T00:00:00Z'", {"id": did})
        s.run("MATCH (an:Analysis {id:$aid}), (x:Decision {id:$id}) "
              "MERGE (an)-[:RECORDED]->(x)", {"aid": aid, "id": did})
    d.close()

def test_lineage_surfaces_linked_decision(seed):
    _link_decision(did="dec-1", aid="an2")
    r = client.post("/api/v1/provenance/lineage", json={"entity": "parkinson disease"})
    assert r.status_code == 200
    top = r.json()["paths"][0]  # an2 is current (confidence 0.90)
    ids = [d["id"] for d in top["decisions"]]
    assert "dec-1" in ids
    dec = next(d for d in top["decisions"] if d["id"] == "dec-1")
    assert dec["decision_outcome"] == "supported"
    assert dec["action_type"] == "hypothesis_verdict"

def test_lineage_decisions_empty_when_unlinked(seed):
    r = client.post("/api/v1/provenance/lineage", json={"entity": "parkinson disease"})
    assert r.status_code == 200
    assert r.json()["paths"][0]["decisions"] == []

def test_lineage_decision_convergence_both_sides(seed):
    # Decision-side link then finding-side link collapse to ONE edge.
    d = _driver()
    with d.session(database=_db()) as s:
        s.run("MATCH (x:Decision {id:'dec-2'}) DETACH DELETE x")
        s.run("MERGE (x:Decision {id:'dec-2'}) SET x.organization_id='org1', x.summary='v', "
              "x.action_type='hypothesis_verdict', x.occurred_at='2026-06-30T00:00:00Z'")
        # decision-side link
        s.run("MATCH (an:Analysis {id:'an2', organization_id:'org1'}), "
              "(x:Decision {id:'dec-2', organization_id:'org1'}) MERGE (an)-[:RECORDED]->(x)")
    # finding-side link (same pair) via the spine writer
    write_finding(d, _link_finding("an2"), database=_db(), link_decision_ids=["dec-2"])
    with d.session(database=_db()) as s:
        n = s.run("MATCH (:Analysis {id:'an2'})-[r:RECORDED]->(:Decision {id:'dec-2'}) "
                  "RETURN count(r) AS n").single()["n"]
    d.close()
    assert n == 1


def test_ingest_decision_links_recorded_analysis_live(seed):
    """#258 Important gap: exercise ingest_decision's real step-3b Cypher (the
    query with `an IS NOT NULL AS linked`) against the real Neo4j, through the
    real endpoint — not the _FakeKG canned rows used elsewhere. A mixed
    known+unknown analysis id proves only the matched one is counted; a direct
    Cypher check plus a lineage GET prove the edge actually landed and
    round-trips through the read side (write/read coherence)."""
    body = {
        "id": "dec-live",
        "tool": "nexus",
        "action_type": "hypothesis_verdict",
        "occurred_at": "2026-06-30T00:00:00Z",
        "summary": "v",
        "decision_outcome": "supported",
        "recorded_by_analysis_ids": ["an2", "missing-analysis"],
    }
    r = client.post("/api/v1/provenance/decisions", json=body)
    assert r.status_code == 201
    assert r.json()["recorded_links_created"] == 1

    d = _driver()
    with d.session(database=_db()) as s:
        n = s.run(
            "MATCH (:Analysis {id:'an2'})-[r:RECORDED]->(:Decision {id:'dec-live'}) "
            "RETURN count(r) AS n").single()["n"]
    d.close()
    assert n == 1

    r2 = client.post("/api/v1/provenance/lineage", json={"entity": "parkinson disease"})
    assert r2.status_code == 200
    top = r2.json()["paths"][0]  # an2 is current (confidence 0.90)
    ids = [dec["id"] for dec in top["decisions"]]
    assert "dec-live" in ids


def test_lineage_decisions_two_distinct_for_one_analysis(seed):
    """One analysis (an2), two distinct decisions linked via the real endpoint —
    exercises the `collect(DISTINCT d {...})` fan-out/regroup in the lineage tail."""
    body_common = {
        "tool": "nexus",
        "action_type": "hypothesis_verdict",
        "occurred_at": "2026-06-30T00:00:00Z",
        "summary": "v",
        "decision_outcome": "supported",
        "recorded_by_analysis_ids": ["an2"],
    }
    for did in ("dec-a", "dec-b"):
        r = client.post("/api/v1/provenance/decisions", json={**body_common, "id": did})
        assert r.status_code == 201
        assert r.json()["recorded_links_created"] == 1

    r = client.post("/api/v1/provenance/lineage", json={"entity": "parkinson disease"})
    assert r.status_code == 200
    top = r.json()["paths"][0]
    ids = [d["id"] for d in top["decisions"]]
    assert len(top["decisions"]) >= 2
    assert "dec-a" in ids and "dec-b" in ids
