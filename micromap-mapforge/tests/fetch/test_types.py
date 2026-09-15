"""C3a (#76) fetch types."""
from pathlib import Path

from micromap_mapforge.fetch.types import FetchError, FetchResult


def test_fetchresult_to_dict_stringifies_path():
    fr = FetchResult(
        local_path=Path("out/data.csv"),
        sha256="abc123",
        url="https://zenodo.org/records/1",
        doi="10.5281/zenodo.1",
        accessed_at="2026-06-12",
        source_name="demo",
        note="",
    )
    d = fr.to_dict()
    assert d["local_path"] == "out/data.csv"   # Path -> str for JSON
    assert d["sha256"] == "abc123"
    assert d["doi"] == "10.5281/zenodo.1"


def test_fetcherror_is_valueerror():
    # The CLI's existing `except ValueError` must catch FetchError.
    assert issubclass(FetchError, ValueError)
