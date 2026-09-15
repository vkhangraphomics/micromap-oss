import pytest
from database.ingestion.utils import resolve_compound_id


def test_prefers_inchi_key():
    result = resolve_compound_id("BRMWTNUJHUMWMS-LURJTMIESA-N", "92105")
    assert result == "INCHIKEY:BRMWTNUJHUMWMS-LURJTMIESA-N"


def test_falls_back_to_pubchem_cid_when_no_inchi_key():
    result = resolve_compound_id(None, "92105")
    assert result == "PUBCHEM:92105"


def test_pubchem_cid_as_integer():
    result = resolve_compound_id(None, 92105)
    assert result == "PUBCHEM:92105"


def test_raises_when_both_absent():
    with pytest.raises(ValueError, match="requires at least one of"):
        resolve_compound_id(None, None)


def test_falls_back_to_pubchem_when_inchi_key_empty_string():
    result = resolve_compound_id("", "92105")
    assert result == "PUBCHEM:92105"


def test_raises_when_both_empty_strings():
    with pytest.raises(ValueError, match="requires at least one of"):
        resolve_compound_id("", "")
