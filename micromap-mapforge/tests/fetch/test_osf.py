"""C3b (#76): OSF node fetch (paginated osfstorage listing)."""
import hashlib

import httpx
import pytest

from micromap_mapforge.fetch import download
from micromap_mapforge.fetch.osf import fetch_osf
from micromap_mapforge.fetch.types import FetchError

DATA = b"x\n1\n"
GUID = "ab12c"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    monkeypatch.setattr(download, "_resolve_ips", lambda host: ["93.184.216.34"])


def _page(entries, next_url):
    return {"data": entries, "links": {"next": next_url}}


def _entry(name, size, dl):
    # Recorded shape of an osfstorage file entry. Pinned fixture.
    return {"attributes": {"name": name, "size": size, "kind": "file"},
            "links": {"download": dl}}


def _handler(req):
    u = str(req.url)
    if u.startswith("https://api.osf.io/v2/nodes/ab12c/files/osfstorage/") and "page=2" not in u:
        return httpx.Response(200, json=_page(
            [_entry("a.csv", len(DATA), "https://files.osf.io/a")],
            "https://api.osf.io/v2/nodes/ab12c/files/osfstorage/?page=2"))
    if "page=2" in u:
        return httpx.Response(200, json=_page(
            [_entry("b.csv", 9, "https://files.osf.io/b")], None))
    return httpx.Response(200, content=DATA)


def _client(handler=_handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_first_across_pages_with_note(tmp_path):
    fr = fetch_osf(GUID, dest_dir=tmp_path, client=_client())
    assert fr.local_path == tmp_path / "a.csv"
    assert fr.sha256 == hashlib.sha256(DATA).hexdigest()
    assert fr.doi == "10.17605/OSF.IO/ab12c"
    assert fr.url == "https://osf.io/ab12c/"
    # Pagination assembled both pages -> 2-file inventory note.
    assert "2 files" in fr.note


def test_fetch_named_file_from_page_two(tmp_path):
    def handler(req):
        if str(req.url) == "https://files.osf.io/b":
            return httpx.Response(200, content=b"hello\n")
        return _handler(req)
    fr = fetch_osf(GUID, file="b.csv", dest_dir=tmp_path, client=_client(handler))
    assert fr.local_path == tmp_path / "b.csv"
    assert fr.note == ""


def test_no_files_raises(tmp_path):
    client = _client(lambda req: httpx.Response(200, json=_page([], None)))
    with pytest.raises(FetchError, match="no files"):
        fetch_osf(GUID, dest_dir=tmp_path, client=client)


def test_pagination_next_private_blocked(monkeypatch, tmp_path):
    # links.next pointing at a private host is SSRF-guarded on the next hop.
    monkeypatch.setattr(download, "_resolve_ips",
                        lambda host: ["10.0.0.1"] if "evil" in host else ["93.184.216.34"])

    def handler(req):
        return httpx.Response(200, json=_page(
            [_entry("a.csv", len(DATA), "https://files.osf.io/a")],
            "https://evil.internal/v2/next"))

    with pytest.raises(FetchError, match="private/link-local"):
        fetch_osf(GUID, dest_dir=tmp_path, client=_client(handler))


def test_entry_missing_name_skipped(tmp_path):
    def handler(req):
        return httpx.Response(200, json=_page(
            [{"attributes": {"size": 5, "kind": "file"},
              "links": {"download": "https://files.osf.io/a"}}],  # no name
            None))
    with pytest.raises(FetchError, match="no files"):
        fetch_osf(GUID, dest_dir=tmp_path, client=_client(handler))
