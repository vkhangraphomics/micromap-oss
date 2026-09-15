"""Regression tests for #255: id-bearing response models must accept the
integer-typed ``ncbi_tax_id`` values that exist for a handful of Taxon nodes
in the graph, rather than raising a ValidationError that 500s the whole page.

The knowledge graph stores ``Taxon.ncbi_tax_id`` as a string for the vast
majority of nodes but as an integer for ~46 of them. Pydantic v2 does not
coerce ``int`` -> ``str`` by default, so before the fix a page window that
included one of those rows (e.g. ``Allisonella`` in ``parkinson disease``'s
taxa) raised ``ValidationError`` -> HTTP 500.
"""
import pytest

from api.models import DiseaseTaxon, LineageNode, MetaboliteProducer, TaxonSummary


@pytest.mark.parametrize(
    "model, kwargs, id_field",
    [
        (DiseaseTaxon, {"taxon_name": "Allisonella"}, "ncbi_id"),
        (MetaboliteProducer, {"taxon_name": "Allisonella"}, "ncbi_id"),
        (TaxonSummary, {"name": "Allisonella"}, "ncbi_tax_id"),
        (LineageNode, {"name": "Allisonella"}, "ncbi_tax_id"),
    ],
)
def test_integer_ncbi_id_is_coerced_to_string(model, kwargs, id_field):
    """An integer NCBI id (as some Taxon nodes store it) is coerced to str
    instead of raising."""
    instance = model(**{**kwargs, id_field: 209879})
    assert getattr(instance, id_field) == "209879"


def test_string_ncbi_id_still_accepted():
    """The common case (string id) is unchanged."""
    assert DiseaseTaxon(taxon_name="Alistipes", ncbi_id="28117").ncbi_id == "28117"


def test_none_ncbi_id_still_accepted():
    """A missing id stays None (the field is Optional)."""
    assert DiseaseTaxon(taxon_name="Alistipes").ncbi_id is None
