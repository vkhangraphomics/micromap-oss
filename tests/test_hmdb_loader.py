import pytest
from unittest.mock import MagicMock
from database.ingestion.hmdb_loader import HMDBLoader


@pytest.fixture
def loader():
    driver = MagicMock()
    session = MagicMock()
    driver.session.return_value.__enter__ = MagicMock(return_value=session)
    driver.session.return_value.__exit__ = MagicMock(return_value=False)
    l = HMDBLoader.__new__(HMDBLoader)
    l.driver = driver
    l.organization_id = "test-org"
    l.batch_size = 100
    l.stats = MagicMock()
    return l


def test_transform_sets_compound_id_from_inchi_key(loader):
    record = {
        "hmdb_id": "HMDB0000001",
        "name": "1-Methylhistidine",
        "inchi_key": "BRMWTNUJHUMWMS-LURJTMIESA-N",
        "pubchem_cid": "92105",
        "biofluid_locations": ["Urine"],
        "tissue_locations": [],
    }
    result = loader.transform(record)
    assert result is not None
    assert result["compound_id"] == "INCHIKEY:BRMWTNUJHUMWMS-LURJTMIESA-N"
    assert "metabolite_id" not in result


def test_transform_falls_back_to_pubchem_when_no_inchi_key(loader):
    record = {
        "hmdb_id": "HMDB0000002",
        "name": "Some compound",
        "inchi_key": None,
        "pubchem_cid": "12345",
        "biofluid_locations": [],
        "tissue_locations": ["Liver"],
    }
    result = loader.transform(record)
    assert result is not None
    assert result["compound_id"] == "PUBCHEM:12345"


def test_transform_returns_none_when_neither_inchi_key_nor_pubchem(loader):
    record = {
        "hmdb_id": "HMDB9999999",
        "name": "Unknown",
        "inchi_key": None,
        "pubchem_cid": None,
        "biofluid_locations": [],
        "tissue_locations": [],
    }
    result = loader.transform(record)
    assert result is None


def test_load_compounds_method_uses_compound_label(loader):
    """_load_compounds must MERGE on :Compound, not :Metabolite."""
    import inspect as ins
    source = ins.getsource(loader._load_compounds)
    assert "Compound" in source
    assert "Metabolite" not in source
