import pytest
from unittest.mock import MagicMock
from database.ingestion.metacyc_loader import MetaCycLoader, _parse_dat_records


def test_parse_dat_records_single_record():
    text = (
        "UNIQUE-ID - CPD-9245\n"
        "COMMON-NAME - glucose\n"
        "INCHI-KEY - InChIKey=WQZGKKKJIJFFOK-GASJEMHNSA-N\n"
        "PUBCHEM-ID - 5793\n"
        "//\n"
    )
    records = list(_parse_dat_records(text))
    assert len(records) == 1
    assert records[0]["UNIQUE-ID"] == ["CPD-9245"]
    assert records[0]["COMMON-NAME"] == ["glucose"]
    assert records[0]["INCHI-KEY"] == ["InChIKey=WQZGKKKJIJFFOK-GASJEMHNSA-N"]


def test_parse_dat_records_multiple_records():
    text = (
        "UNIQUE-ID - CPD-001\nCOMMON-NAME - A\n//\n"
        "UNIQUE-ID - CPD-002\nCOMMON-NAME - B\n//\n"
    )
    records = list(_parse_dat_records(text))
    assert len(records) == 2


@pytest.fixture
def loader():
    l = MetaCycLoader.__new__(MetaCycLoader)
    l.organization_id = "test-org"
    l.stats = MagicMock()
    return l


def test_transform_compound_sets_compound_id_from_inchi_key(loader):
    raw = {
        "UNIQUE-ID": ["CPD-9245"],
        "COMMON-NAME": ["glucose"],
        "INCHI-KEY": ["InChIKey=WQZGKKKJIJFFOK-GASJEMHNSA-N"],
        "PUBCHEM-ID": ["5793"],
        "_type": "compound",
    }
    result = loader.transform(raw)
    assert result is not None
    assert result["compound_id"] == "INCHIKEY:WQZGKKKJIJFFOK-GASJEMHNSA-N"


def test_transform_compound_falls_back_to_pubchem(loader):
    raw = {
        "UNIQUE-ID": ["CPD-9245"],
        "COMMON-NAME": ["some compound"],
        "INCHI-KEY": [],
        "PUBCHEM-ID": ["5793"],
        "_type": "compound",
    }
    result = loader.transform(raw)
    assert result is not None
    assert result["compound_id"] == "PUBCHEM:5793"


def test_transform_skips_compound_without_ids(loader):
    raw = {
        "UNIQUE-ID": ["CPD-NOID"],
        "COMMON-NAME": ["no ids"],
        "INCHI-KEY": [],
        "PUBCHEM-ID": [],
        "_type": "compound",
    }
    result = loader.transform(raw)
    assert result is None


def test_transform_pathway_sets_pathway_id(loader):
    raw = {
        "UNIQUE-ID": ["GLYCOLYSIS"],
        "COMMON-NAME": ["Glycolysis"],
        "_type": "pathway",
    }
    result = loader.transform(raw)
    assert result is not None
    assert result["pathway_id"] == "METACYC:GLYCOLYSIS"


def test_transform_compound_extracts_in_pathway(loader):
    raw = {
        "UNIQUE-ID": ["CPD-9245"],
        "COMMON-NAME": ["glucose"],
        "INCHI-KEY": ["InChIKey=WQZGKKKJIJFFOK-GASJEMHNSA-N"],
        "PUBCHEM-ID": ["5793"],
        "IN-PATHWAY": ["GLYCOLYSIS", "PWY-5484"],
        "_type": "compound",
    }
    result = loader.transform(raw)
    assert result is not None
    assert "METACYC:GLYCOLYSIS" in result["_in_pathways"]
    assert "METACYC:PWY-5484" in result["_in_pathways"]
