import pytest
from unittest.mock import MagicMock
from database.ingestion.kegg_loader import KEGGLoader


@pytest.fixture
def loader():
    l = KEGGLoader.__new__(KEGGLoader)
    l.organization_id = "test-org"
    l.stats = MagicMock()
    return l


def test_load_compound_cypher_uses_compound_label(loader):
    import inspect as ins
    source = ins.getsource(loader._load_compound)
    assert ":Compound" in source
    assert ":Metabolite" not in source


def test_load_compound_cypher_merges_on_compound_id(loader):
    import inspect as ins
    source = ins.getsource(loader._load_compound)
    assert "compound_id" in source
    assert "metabolite_id" not in source
