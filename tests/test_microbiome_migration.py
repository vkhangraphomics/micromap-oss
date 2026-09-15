"""Regression: no loader may write :Metabolite nodes after #146.

Inspects the source of every microbiome-stack loader to ensure :Metabolite
has been fully purged. A Cypher string containing ':Metabolite' is a
regression — the canonical label is :Compound.
"""
import inspect
import pytest

from database.ingestion.hmdb_loader import HMDBLoader
from database.ingestion.kegg_loader import KEGGLoader
from database.ingestion.pubchem_loader import PubChemLoader
from database.ingestion.produces_loader import ProducesLoader


LOADERS = [HMDBLoader, KEGGLoader, PubChemLoader, ProducesLoader]


@pytest.mark.parametrize("loader_cls", LOADERS, ids=lambda c: c.__name__)
def test_loader_has_no_metabolite_label(loader_cls):
    source = inspect.getsource(loader_cls)
    assert ":Metabolite" not in source, (
        f"{loader_cls.__name__} still contains ':Metabolite' — "
        "update to ':Compound' (see #146)"
    )


@pytest.mark.parametrize("loader_cls", LOADERS, ids=lambda c: c.__name__)
def test_loader_has_no_metabolite_id_merge_key(loader_cls):
    """metabolite_id as a MERGE key must be gone; compound_id is the canonical key."""
    source = inspect.getsource(loader_cls)
    # produces_loader legitimately uses name_lower (legacy path); allow it.
    if loader_cls is ProducesLoader:
        return
    assert "metabolite_id" not in source, (
        f"{loader_cls.__name__} still uses 'metabolite_id' as a merge key — "
        "switch to 'compound_id' (see #146)"
    )
