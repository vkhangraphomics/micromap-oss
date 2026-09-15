"""C3a (#76): direct-URL fetch."""
import hashlib

import httpx
import pytest

from micromap_mapforge.fetch import download
from micromap_mapforge.fetch.url import fetch_url

PAYLOAD = b"a,b\n1,2\n"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    monkeypatch.setattr(download, "_resolve_ips", lambda host: ["93.184.216.34"])


def _client():
    return httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, content=PAYLOAD)))


def test_fetch_url_names_file_from_path(tmp_path):
    fr = fetch_url("https://example.org/supp/table.csv", dest_dir=tmp_path, client=_client())
    assert fr.local_path == tmp_path / "table.csv"
    assert fr.local_path.read_bytes() == PAYLOAD
    assert fr.sha256 == hashlib.sha256(PAYLOAD).hexdigest()
    assert fr.url == "https://example.org/supp/table.csv"
    assert fr.doi is None
    assert fr.source_name == "table"
    assert fr.note == ""


def test_fetch_url_fallback_name_when_no_segment(tmp_path):
    fr = fetch_url("https://example.org/", dest_dir=tmp_path, client=_client())
    assert fr.local_path == tmp_path / "download"
