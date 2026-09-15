"""
Tests for the PubChem loader.

Focused coverage of the Compound→Metabolite SAME_AS bridge introduced for
issue #46. The PubChem loader was already creating SAME_AS edges from
Compound to Drug; this PR extends it to also bridge Compound to existing
Metabolite nodes via InChI key or pubchem_cid.
"""

from unittest.mock import MagicMock

import pytest

from database.ingestion.pubchem_loader import PubChemLoader


@pytest.fixture
def loader():
    """PubChemLoader with a mock driver and patched execute_cypher."""
    driver = MagicMock()
    inst = PubChemLoader(driver=driver, organization_id="test-org")
    inst._execute_calls = []
    inst._execute_responses = []

    def fake_execute(cypher, params=None, write=True):
        inst._execute_calls.append((cypher, params or {}))
        if inst._execute_responses:
            return inst._execute_responses.pop(0)
        return []

    inst.execute_cypher = fake_execute
    return inst


def test_link_to_metabolites_returns_zero_when_no_xrefs(loader):
    """No compound has an InChI key or pubchem_cid — skip the query entirely."""
    batch = [{"compound_id": "PUBCHEM:1"}]  # no inchi_key, no pubchem_cid
    assert loader._link_to_metabolites(batch) == 0
    assert loader._execute_calls == []


def test_link_to_metabolites_passes_inchi_key_when_present(loader):
    """Compound with InChI key shows up in the params with the key intact."""
    loader._execute_responses = [[{"count": 1}]]

    batch = [{
        "compound_id": "PUBCHEM:1",
        "inchi_key": "ABCDEF-GHIJKL-NM",
        "pubchem_cid": 123,
    }]
    n = loader._link_to_metabolites(batch)

    assert n == 1
    assert len(loader._execute_calls) == 1
    cypher, params = loader._execute_calls[0]
    assert "other_compound.inchi_key = c.inchi_key" in cypher
    assert "other_compound.pubchem_cid = c.pubchem_cid" in cypher
    assert params["compounds"] == [{
        "compound_id": "PUBCHEM:1",
        "inchi_key": "ABCDEF-GHIJKL-NM",
        "pubchem_cid": 123,
    }]


def test_link_to_metabolites_includes_pubchem_cid_only_compounds(loader):
    """A compound with no InChI key but a pubchem_cid still produces a query row."""
    loader._execute_responses = [[{"count": 1}]]

    batch = [{
        "compound_id": "PUBCHEM:1",
        "inchi_key": None,
        "pubchem_cid": 999,
    }]
    n = loader._link_to_metabolites(batch)

    assert n == 1
    _cypher, params = loader._execute_calls[0]
    assert params["compounds"][0]["pubchem_cid"] == 999
    assert params["compounds"][0]["inchi_key"] is None


def test_link_to_metabolites_filters_compounds_with_no_id_or_xref(loader):
    """Compounds missing both compound_id and any xref are excluded entirely."""
    loader._execute_responses = [[{"count": 1}]]

    batch = [
        {"compound_id": "PUBCHEM:1", "inchi_key": "KEY1"},
        {"inchi_key": "KEY2"},                       # no compound_id — drop
        {"compound_id": "PUBCHEM:3"},                # no xref — drop
        {"compound_id": "PUBCHEM:4", "pubchem_cid": 4},
    ]
    loader._link_to_metabolites(batch)

    _cypher, params = loader._execute_calls[0]
    compound_ids = [c["compound_id"] for c in params["compounds"]]
    assert compound_ids == ["PUBCHEM:1", "PUBCHEM:4"]


def test_load_batch_calls_link_to_metabolites(loader):
    """Verify load_batch wiring: _link_to_metabolites is invoked on every batch."""
    # Stub the methods called by load_batch so this test stays focused
    loader._load_compounds = MagicMock(return_value=0)
    loader._load_bioactivity = MagicMock(return_value=(0, 0))
    loader._link_to_chembl = MagicMock(return_value=0)
    loader._link_to_metabolites = MagicMock(return_value=2)

    result = loader.load_batch([{"compound_id": "PUBCHEM:1", "inchi_key": "KEY"}])

    loader._link_to_metabolites.assert_called_once()
    assert result["relationships_created"] >= 2


def test_transform_prefers_inchi_key_for_compound_id():
    from database.ingestion.pubchem_loader import PubChemLoader
    loader = PubChemLoader.__new__(PubChemLoader)
    loader.organization_id = "test-org"
    record = {
        "compound": {
            "CID": 5793,
            "IUPACName": "glucose",
            "InChIKey": "WQZGKKKJIJFFOK-GASJEMHNSA-N",
            "MolecularFormula": "C6H12O6",
        },
        "bioactivity": [],
    }
    result = loader.transform(record)
    assert result is not None
    assert result["compound_id"] == "INCHIKEY:WQZGKKKJIJFFOK-GASJEMHNSA-N"


def test_transform_falls_back_to_pubchem_when_no_inchi_key():
    from database.ingestion.pubchem_loader import PubChemLoader
    loader = PubChemLoader.__new__(PubChemLoader)
    loader.organization_id = "test-org"
    record = {
        "compound": {
            "CID": 5793,
            "IUPACName": "glucose",
            "InChIKey": None,
        },
        "bioactivity": [],
    }
    result = loader.transform(record)
    assert result is not None
    assert result["compound_id"] == "PUBCHEM:5793"
