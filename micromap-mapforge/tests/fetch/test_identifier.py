"""C3a/C3b (#76): source-arg classification."""
import pytest

from micromap_mapforge.fetch.identifier import classify


@pytest.mark.parametrize("arg, kind, provider, id_, href", [
    ("doi:10.5281/zenodo.1234567", "provider", "zenodo", "1234567", None),
    ("10.5281/zenodo.7654321", "provider", "zenodo", "7654321", None),
    ("DOI:10.5281/ZENODO.1234567", "provider", "zenodo", "1234567", None),
    ("https://zenodo.org/records/42", "provider", "zenodo", "42", None),
    ("https://zenodo.org/record/42", "provider", "zenodo", "42", None),
    ("https://example.org/supp/table.csv", "url", None, None, "https://example.org/supp/table.csv"),
    ("http://example.org/a.tsv", "url", None, None, "http://example.org/a.tsv"),
    ("10.1038/nature12373", "unsupported_doi", None, None, None),   # a real, unsupported publisher DOI
    ("wb://proj/data.csv", "unsupported_wb", None, None, None),
    ("data.csv", "local", None, None, None),
    ("/abs/path/data.tsv", "local", None, None, None),
    ("doi:10.6084/m9.figshare.1234567", "provider", "figshare", "1234567", None),
    ("10.6084/m9.figshare.987", "provider", "figshare", "987", None),
    ("https://figshare.com/articles/dataset/title/555", "provider", "figshare", "555", None),
    ("doi:10.5061/dryad.abc123", "provider", "dryad", "10.5061/dryad.abc123", None),
    ("10.5061/dryad.xy9", "provider", "dryad", "10.5061/dryad.xy9", None),
    ("https://datadryad.org/stash/dataset/doi:10.5061/dryad.abc123", "provider", "dryad", "10.5061/dryad.abc123", None),
    ("doi:10.17605/OSF.IO/AB12C", "provider", "osf", "AB12C", None),
    ("10.17605/OSF.IO/xy9z", "provider", "osf", "xy9z", None),
    ("https://osf.io/ab12c/", "provider", "osf", "ab12c", None),
])
def test_classify(arg, kind, provider, id_, href):
    c = classify(arg)
    assert c.kind == kind
    assert c.provider == provider
    assert c.id == id_
    assert c.href == href
