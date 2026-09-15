"""C3a (#76): Zenodo deposit fetch (list cheap, download one)."""
import hashlib

import httpx
import pytest

from micromap_mapforge.fetch import download
from micromap_mapforge.fetch.types import FetchError
from micromap_mapforge.fetch.zenodo import fetch_zenodo

DATA_A = b"tax_id\n562\n"
DATA_B = b"doid\nDOID:1\n"


def _record(files):
    return {
        "doi": "10.5281/zenodo.42",
        "metadata": {"title": "Demo deposit"},
        "links": {"html": "https://zenodo.org/records/42"},
        "files": files,
    }


def _two_file_handler(req):
    if req.url.path == "/api/records/42":
        return httpx.Response(200, json=_record([
            {"key": "counts.tsv", "size": len(DATA_A),
             "links": {"self": "https://zenodo.org/api/records/42/files/counts.tsv/content"}},
            {"key": "meta.tsv", "size": len(DATA_B),
             "links": {"self": "https://zenodo.org/api/records/42/files/meta.tsv/content"}},
        ]))
    if req.url.path.endswith("/counts.tsv/content"):
        return httpx.Response(200, content=DATA_A)
    if req.url.path.endswith("/meta.tsv/content"):
        return httpx.Response(200, content=DATA_B)
    return httpx.Response(404)


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    monkeypatch.setattr(download, "_resolve_ips", lambda host: ["93.184.216.34"])


def _client():
    return httpx.Client(transport=httpx.MockTransport(_two_file_handler))


def test_fetch_first_file_with_inventory_note(tmp_path):
    fr = fetch_zenodo("42", dest_dir=tmp_path, client=_client())
    assert fr.local_path == tmp_path / "counts.tsv"
    assert fr.local_path.read_bytes() == DATA_A
    assert fr.sha256 == hashlib.sha256(DATA_A).hexdigest()
    assert fr.doi == "10.5281/zenodo.42"
    assert fr.url == "https://zenodo.org/records/42"
    assert fr.source_name == "Demo deposit"
    assert "2 files" in fr.note and "--file" in fr.note


def test_fetch_named_file_no_note(tmp_path):
    fr = fetch_zenodo("42", file="meta.tsv", dest_dir=tmp_path, client=_client())
    assert fr.local_path == tmp_path / "meta.tsv"
    assert fr.local_path.read_bytes() == DATA_B
    assert fr.note == ""   # explicit selection suppresses the inventory note


def test_unknown_file_raises_with_listing(tmp_path):
    with pytest.raises(FetchError, match="no file 'nope.tsv'"):
        fetch_zenodo("42", file="nope.tsv", dest_dir=tmp_path, client=_client())


def test_oversize_rejected_before_download(tmp_path):
    def handler(req):
        return httpx.Response(200, json=_record([
            {"key": "huge.bin", "size": 10**12,
             "links": {"self": "https://zenodo.org/api/records/42/files/huge.bin/content"}},
        ]))
    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(FetchError, match="exceeds"):
        fetch_zenodo("42", dest_dir=tmp_path, client=client)


def test_no_files_raises(tmp_path):
    def handler(req):
        return httpx.Response(200, json=_record([]))
    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(FetchError, match="no files"):
        fetch_zenodo("42", dest_dir=tmp_path, client=client)


def test_skip_redownload_when_file_present(tmp_path):
    # Pre-seed the destination with the right size; fetch must not overwrite.
    (tmp_path / "counts.tsv").write_bytes(DATA_A)
    fr = fetch_zenodo("42", file="counts.tsv", dest_dir=tmp_path, client=_client())
    assert fr.sha256 == hashlib.sha256(DATA_A).hexdigest()


def test_non_json_response_raises_fetcherror(tmp_path):
    def handler(req):
        # Zenodo maintenance page: 200 OK but an HTML body, not JSON.
        return httpx.Response(200, content=b"<html>down for maintenance</html>")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(FetchError, match="not valid JSON"):
        fetch_zenodo("42", dest_dir=tmp_path, client=client)
