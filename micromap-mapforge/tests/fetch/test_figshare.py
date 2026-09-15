"""C3b (#76): Figshare article fetch."""
import hashlib

import httpx
import pytest

from micromap_mapforge.fetch import download
from micromap_mapforge.fetch.figshare import fetch_figshare
from micromap_mapforge.fetch.types import FetchError

DATA = b"gene,val\nA,1\n"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    monkeypatch.setattr(download, "_resolve_ips", lambda host: ["93.184.216.34"])


def _article(files):
    # Recorded shape of GET /v2/articles/<id>. Pinned fixture; verify at smoke.
    return {
        "title": "Demo article",
        "doi": "10.6084/m9.figshare.42",
        "figshare_url": "https://figshare.com/articles/dataset/demo/42",
        "files": files,
    }


def _handler(req):
    if req.url.path == "/v2/articles/42":
        return httpx.Response(200, json=_article([
            {"name": "counts.csv", "size": len(DATA),
             "download_url": "https://ndownloader.figshare.com/files/100"},
            {"name": "meta.csv", "size": 7,
             "download_url": "https://ndownloader.figshare.com/files/101"},
        ]))
    return httpx.Response(200, content=DATA)


def _client(handler=_handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_first_file_with_note(tmp_path):
    fr = fetch_figshare("42", dest_dir=tmp_path, client=_client())
    assert fr.local_path == tmp_path / "counts.csv"
    assert fr.local_path.read_bytes() == DATA
    assert fr.sha256 == hashlib.sha256(DATA).hexdigest()
    assert fr.doi == "10.6084/m9.figshare.42"
    assert fr.url == "https://figshare.com/articles/dataset/demo/42"
    assert fr.source_name == "Demo article"
    assert "2 files" in fr.note


def test_fetch_named_file(tmp_path):
    fr = fetch_figshare("42", file="meta.csv", dest_dir=tmp_path, client=_client())
    assert fr.local_path == tmp_path / "meta.csv"
    assert fr.note == ""


def test_no_files_raises(tmp_path):
    client = _client(lambda req: httpx.Response(200, json=_article([])))
    with pytest.raises(FetchError, match="no files"):
        fetch_figshare("42", dest_dir=tmp_path, client=client)


def test_file_missing_download_url_skipped(tmp_path):
    # A malformed file entry (no download_url) is skipped, not a KeyError crash.
    client = _client(lambda req: httpx.Response(200, json=_article([
        {"name": "broken.csv", "size": 5},  # no download_url
    ])))
    with pytest.raises(FetchError, match="no files"):
        fetch_figshare("42", dest_dir=tmp_path, client=client)
