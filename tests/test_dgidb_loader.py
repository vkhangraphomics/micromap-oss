"""
Tests for the DGIdb (Drug-Gene Interaction Database) loader (#375, #384-adjacent).

DGIdb v5's GraphQL API (https://dgidb.org/api/graphql) is queried directly —
no name list required, full cursor-paginated crawl of `drugs(first, after)`.
Drug identity: a `chembl:<ID>` concept ID merges onto the existing
ChEMBL-sourced `Drug{drug_id:"CHEMBL:<ID>"}` node (exact-ID overlap, not fuzzy
name matching); anything else gets its own `Drug{drug_id:"DGIDB:<conceptId>"}`.
Gene identity keys on `name` (HGNC symbol), matching the existing `--derive`
convention. The relationship type is `DGIDB_INTERACTS_WITH` — deliberately not
`INTERACTS_WITH`, which `semmeddb_loader.py` already uses for unrelated mined
literature predicates with a different shape (see CLAUDE.md's CHILD_OF/HAS_GENE
warning about relationship-name collisions).
"""

from unittest.mock import MagicMock

import pytest

from database.ingestion.dgidb_loader import DGIdbLoader


@pytest.fixture
def loader():
    """DGIdbLoader with a mock driver and patched execute_cypher/fetch_with_retry."""
    driver = MagicMock()
    inst = DGIdbLoader(driver=driver, organization_id="test-org")
    inst._execute_calls = []
    inst._execute_responses = []

    def fake_execute(cypher, params=None, write=True):
        inst._execute_calls.append((cypher, params or {}))
        if inst._execute_responses:
            return inst._execute_responses.pop(0)
        return []

    inst.execute_cypher = fake_execute
    return inst


# ---------------------------------------------------------------------------
# source_name
# ---------------------------------------------------------------------------

def test_source_name_is_dgidb(loader):
    assert loader.source_name == "DGIdb"


# ---------------------------------------------------------------------------
# transform()
# ---------------------------------------------------------------------------

def _raw_record(**overrides):
    record = {
        "drug": {"conceptId": "chembl:CHEMBL1201", "name": "ERYTHROMYCIN STEARATE", "approved": True},
        "interaction": {
            "gene": {"conceptId": "hgnc:6502", "name": "RPSA"},
            "interactionScore": 0.3077441249609461,
            "interactionTypes": [{"type": "inhibitor", "directionality": "INHIBITORY"}],
            "publications": [{"pmid": 24869598}],
            "sources": [{"sourceDbName": "ChEMBL", "license": "CC BY-SA 3.0"}],
        },
    }
    record.update(overrides)
    return record


def test_transform_chembl_concept_id_merges_onto_existing_chembl_drug(loader):
    result = loader.transform(_raw_record())

    assert result["drug_merge_id"] == "CHEMBL:CHEMBL1201"
    assert result["drug_name"] == "ERYTHROMYCIN STEARATE"
    assert result["drug_concept_id"] == "chembl:CHEMBL1201"


def test_transform_non_chembl_concept_id_gets_dgidb_namespaced_id(loader):
    record = _raw_record(drug={"conceptId": "drugbank:DB07119", "name": "SOME DRUG", "approved": False})

    result = loader.transform(record)

    assert result["drug_merge_id"] == "DGIDB:drugbank:DB07119"


def test_transform_extracts_gene_and_interaction_fields(loader):
    result = loader.transform(_raw_record())

    assert result["gene_name"] == "RPSA"
    assert result["interaction_score"] == pytest.approx(0.3077441249609461)
    assert result["interaction_types"] == ["inhibitor"]
    assert result["interaction_directions"] == ["INHIBITORY"]
    assert result["pmids"] == [24869598]
    assert result["source_names"] == ["ChEMBL"]


def test_transform_returns_none_when_drug_concept_id_missing(loader):
    record = _raw_record(drug={"conceptId": None, "name": "NO ID", "approved": False})
    assert loader.transform(record) is None


def test_transform_returns_none_when_gene_missing(loader):
    record = _raw_record()
    record["interaction"] = dict(record["interaction"], gene=None)
    assert loader.transform(record) is None


def test_transform_dedupes_source_names_and_handles_missing_types(loader):
    record = _raw_record()
    record["interaction"] = dict(
        record["interaction"],
        interactionTypes=[],
        sources=[{"sourceDbName": "CIViC", "license": None}, {"sourceDbName": "CIViC", "license": None}],
    )

    result = loader.transform(record)

    assert result["interaction_types"] == []
    assert result["interaction_directions"] == []
    assert result["source_names"] == ["CIViC"]


# ---------------------------------------------------------------------------
# load_batch()
# ---------------------------------------------------------------------------

def _transformed(**overrides):
    record = {
        "drug_merge_id": "CHEMBL:CHEMBL1201",
        "drug_name": "ERYTHROMYCIN STEARATE",
        "drug_concept_id": "chembl:CHEMBL1201",
        "drug_approved": True,
        "gene_name": "RPSA",
        "gene_concept_id": "hgnc:6502",
        "interaction_score": 0.31,
        "interaction_types": ["inhibitor"],
        "interaction_directions": ["INHIBITORY"],
        "pmids": [24869598],
        "source_names": ["ChEMBL"],
    }
    record.update(overrides)
    return record


def test_load_batch_empty_returns_zero_counts(loader):
    result = loader.load_batch([])
    assert result == {"nodes_created": 0, "relationships_created": 0}
    assert loader._execute_calls == []


def test_load_batch_merges_drug_gene_and_relationship(loader):
    loader._execute_responses = [
        [{"count": 1}],  # drug merge
        [{"count": 1}],  # gene merge
        [{"count": 1}],  # relationship merge
    ]

    result = loader.load_batch([_transformed()])

    assert result == {"nodes_created": 2, "relationships_created": 1}
    assert len(loader._execute_calls) == 3

    drug_cypher, drug_params = loader._execute_calls[0]
    assert "MERGE (drug:Drug {drug_id: d.drug_id})" in drug_cypher
    assert drug_params["drugs"] == [{
        "drug_id": "CHEMBL:CHEMBL1201",
        "name": "ERYTHROMYCIN STEARATE",
        "dgidb_concept_id": "chembl:CHEMBL1201",
        "approved": True,
        "organization_id": "test-org",
    }]

    gene_cypher, gene_params = loader._execute_calls[1]
    assert "MERGE (gene:Gene {name: g.name})" in gene_cypher
    assert gene_params["genes"] == [{
        "name": "RPSA",
        "organization_id": "test-org",
    }]

    rel_cypher, rel_params = loader._execute_calls[2]
    assert "MERGE (drug)-[rel:DGIDB_INTERACTS_WITH]->(gene)" in rel_cypher
    assert rel_params["relationships"] == [{
        "drug_id": "CHEMBL:CHEMBL1201",
        "gene_name": "RPSA",
        "interaction_score": 0.31,
        "interaction_types": ["inhibitor"],
        "interaction_directions": ["INHIBITORY"],
        "pmids": [24869598],
        "source_names": ["ChEMBL"],
        "source": "dgidb",
    }]


def test_load_batch_dedupes_repeated_drug_gene_pairs(loader):
    loader._execute_responses = [
        [{"count": 1}],
        [{"count": 1}],
        [{"count": 1}],
    ]

    result = loader.load_batch([_transformed(), _transformed()])

    _cypher, drug_params = loader._execute_calls[0]
    assert len(drug_params["drugs"]) == 1
    _cypher, gene_params = loader._execute_calls[1]
    assert len(gene_params["genes"]) == 1
    _cypher, rel_params = loader._execute_calls[2]
    assert len(rel_params["relationships"]) == 1
    assert result == {"nodes_created": 2, "relationships_created": 1}


# ---------------------------------------------------------------------------
# extract()
# ---------------------------------------------------------------------------

def _page(nodes, has_next, end_cursor):
    return {
        "data": {
            "drugs": {
                "totalCount": 2,
                "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
                "nodes": nodes,
            }
        }
    }


def test_extract_paginates_and_skips_drugs_with_no_interactions(loader):
    page1 = _page(
        nodes=[
            {"conceptId": "chembl:NOINT", "name": "NO INTERACTIONS", "approved": False, "interactions": []},
            {
                "conceptId": "chembl:CHEMBL1201",
                "name": "ERYTHROMYCIN STEARATE",
                "approved": True,
                "interactions": [
                    {
                        "gene": {"conceptId": "hgnc:6502", "name": "RPSA"},
                        "interactionScore": 0.31,
                        "interactionTypes": [],
                        "publications": [],
                        "sources": [],
                    }
                ],
            },
        ],
        has_next=True,
        end_cursor="CURSOR1",
    )
    page2 = _page(nodes=[], has_next=False, end_cursor=None)

    calls = []

    def fake_fetch(url, params=None, max_retries=3, method="GET", json=None):
        calls.append(json)
        return page1 if len(calls) == 1 else page2

    loader.fetch_with_retry = fake_fetch

    records = list(loader.extract())

    assert len(records) == 1
    assert records[0]["drug"]["conceptId"] == "chembl:CHEMBL1201"
    assert records[0]["interaction"]["gene"]["name"] == "RPSA"

    # Second call must page forward using the cursor from the first response.
    assert len(calls) == 2
    assert calls[0]["variables"]["after"] is None
    assert calls[1]["variables"]["after"] == "CURSOR1"


def test_extract_yields_one_record_per_interaction(loader):
    page1 = _page(
        nodes=[
            {
                "conceptId": "chembl:MULTI",
                "name": "MULTI-TARGET DRUG",
                "approved": True,
                "interactions": [
                    {"gene": {"conceptId": "hgnc:1", "name": "GENE1"}, "interactionScore": 0.1,
                     "interactionTypes": [], "publications": [], "sources": []},
                    {"gene": {"conceptId": "hgnc:2", "name": "GENE2"}, "interactionScore": 0.2,
                     "interactionTypes": [], "publications": [], "sources": []},
                ],
            },
        ],
        has_next=False,
        end_cursor=None,
    )

    loader.fetch_with_retry = lambda url, params=None, max_retries=3, method="GET", json=None: page1

    records = list(loader.extract())

    assert len(records) == 2
    assert {r["interaction"]["gene"]["name"] for r in records} == {"GENE1", "GENE2"}
