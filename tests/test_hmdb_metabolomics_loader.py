import pytest
from unittest.mock import MagicMock
from database.ingestion.hmdb_metabolomics_loader import HMDBMetabolomicsLoader


@pytest.fixture
def loader():
    l = HMDBMetabolomicsLoader.__new__(HMDBMetabolomicsLoader)
    l.organization_id = "test-org"
    l.stats = MagicMock()
    return l


def _record(**kwargs):
    base = {
        "hmdb_id": "HMDB0000001",
        "name": "1-Methylhistidine",
        "inchi_key": "BRMWTNUJHUMWMS-LURJTMIESA-N",
        "pubchem_cid": "92105",
        "biofluid_locations": ["Urine"],
        "tissue_locations": [],
        "is_endogenous": True,
        "_diseases": [],
        "_pathways": [],
    }
    base.update(kwargs)
    return base


def test_transform_human_endogenous_record(loader):
    result = loader.transform(_record())
    assert result is not None
    assert result["compound_id"] == "INCHIKEY:BRMWTNUJHUMWMS-LURJTMIESA-N"
    assert result["hmdb_id"] == "HMDB0000001"


def test_transform_filters_no_location_and_not_endogenous(loader):
    result = loader.transform(_record(
        biofluid_locations=[],
        tissue_locations=[],
        is_endogenous=False,
    ))
    assert result is None


def test_transform_accepts_tissue_location_without_endogenous_flag(loader):
    result = loader.transform(_record(
        biofluid_locations=[],
        tissue_locations=["Liver"],
        is_endogenous=False,
    ))
    assert result is not None


def test_transform_returns_none_when_no_merge_key(loader):
    result = loader.transform(_record(inchi_key=None, pubchem_cid=None))
    assert result is None


def test_transform_includes_found_in_locations(loader):
    result = loader.transform(_record(
        biofluid_locations=["Urine", "Blood"],
        tissue_locations=["Liver"],
    ))
    assert result is not None
    assert set(result["_locations"]) == {"Urine", "Blood", "Liver"}


def test_transform_includes_biomarker_diseases(loader):
    result = loader.transform(_record(
        _diseases=[{"name": "Type 2 Diabetes", "evidence": "confirmed"}],
    ))
    assert result is not None
    assert len(result["_diseases"]) == 1
    assert result["_diseases"][0]["name"] == "Type 2 Diabetes"


def test_transform_passes_through_pathways_with_kegg_id(loader):
    result = loader.transform(_record(
        _pathways=[{"name": "Glycolysis", "kegg_id": "map00010", "smpdb_id": None}],
    ))
    assert result is not None
    assert len(result["_pathways"]) == 1
    assert result["_pathways"][0]["kegg_id"] == "map00010"
