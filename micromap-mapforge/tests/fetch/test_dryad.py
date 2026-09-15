"""C3b (#76): Dryad dataset fetch (two-step: dataset -> version -> files)."""
import hashlib

import httpx
import pytest

from micromap_mapforge.fetch import download
from micromap_mapforge.fetch.dryad import fetch_dryad
from micromap_mapforge.fetch.types import FetchError

DATA = b"site,n\nA,3\n"
DOI = "10.5061/dryad.abc123"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    monkeypatch.setattr(download, "_resolve_ips", lambda host: ["93.184.216.34"])


def _handler(req):
    p = req.url.path
    # Step 1: dataset metadata -> latest version link. Pinned fixture.
    # Match the percent-encoded path (httpx decodes `.path`; `.raw_path` keeps
    # the encoding) — this asserts the provider URL-encodes the `doi:`-prefixed DOI.
    if req.url.raw_path == b"/api/v2/datasets/doi%3A10.5061%2Fdryad.abc123":
        return httpx.Response(200, json={
            "title": "Dryad demo",
            "_links": {"stash:version": {"href": "/api/v2/versions/777"}},
        })
    # Step 2: version files listing.
    if p == "/api/v2/versions/777/files":
        return httpx.Response(200, json={"_embedded": {"stash:files": [
            {"path": "data.csv", "size": len(DATA),
             "_links": {"stash:download": {"href": "/api/v2/files/901/download"}}},
            {"path": "readme.txt", "size": 4,
             "_links": {"stash:download": {"href": "/api/v2/files/902/download"}}},
        ]}})
    # Step 3: file bytes.
    if p == "/api/v2/files/901/download":
        return httpx.Response(200, content=DATA)
    return httpx.Response(200, content=b"other")


def _client(handler=_handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_first_file_with_note(tmp_path):
    fr = fetch_dryad(DOI, dest_dir=tmp_path, client=_client())
    assert fr.local_path == tmp_path / "data.csv"
    assert fr.local_path.read_bytes() == DATA
    assert fr.sha256 == hashlib.sha256(DATA).hexdigest()
    assert fr.doi == DOI
    assert fr.url == "https://datadryad.org/stash/dataset/doi:10.5061/dryad.abc123"
    assert fr.source_name == "Dryad demo"
    assert "2 files" in fr.note


def test_fetch_named_file(tmp_path):
    def handler(req):
        if req.url.path == "/api/v2/files/902/download":
            return httpx.Response(200, content=b"hi\n")
        return _handler(req)
    fr = fetch_dryad(DOI, file="readme.txt", dest_dir=tmp_path, client=_client(handler))
    assert fr.local_path == tmp_path / "readme.txt"
    assert fr.note == ""


def test_missing_version_link_raises(tmp_path):
    def handler(req):
        return httpx.Response(200, json={"title": "x", "_links": {}})
    with pytest.raises(FetchError, match="no version"):
        fetch_dryad(DOI, dest_dir=tmp_path, client=_client(handler))


def test_version_href_host_pivot_blocked(monkeypatch, tmp_path):
    # A malicious version_href that pivots the host (userinfo trick) is blocked
    # by the SSRF guard on the second get_json hop.
    monkeypatch.setattr(download, "_resolve_ips",
                        lambda host: ["10.0.0.1"] if "evil" in host else ["93.184.216.34"])

    def handler(req):
        if req.url.raw_path == b"/api/v2/datasets/doi%3A10.5061%2Fdryad.abc123":
            return httpx.Response(200, json={
                "title": "x", "_links": {"stash:version": {"href": "@evil.com/x"}}})
        return httpx.Response(200, content=DATA)

    with pytest.raises(FetchError, match="private/link-local"):
        fetch_dryad(DOI, dest_dir=tmp_path, client=_client(handler))


def test_file_missing_path_skipped(tmp_path):
    def handler(req):
        if req.url.raw_path == b"/api/v2/datasets/doi%3A10.5061%2Fdryad.abc123":
            return httpx.Response(200, json={
                "title": "x", "_links": {"stash:version": {"href": "/api/v2/versions/777"}}})
        if req.url.path == "/api/v2/versions/777/files":
            return httpx.Response(200, json={"_embedded": {"stash:files": [
                {"size": 5, "_links": {"stash:download": {"href": "/api/v2/files/9/download"}}},
            ]}})  # entry has no "path"
        return httpx.Response(200, content=DATA)
    with pytest.raises(FetchError, match="no files"):
        fetch_dryad(DOI, dest_dir=tmp_path, client=_client(handler))
