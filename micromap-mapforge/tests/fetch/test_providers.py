"""C3b (#76): shared provider helpers."""
import hashlib

import httpx
import pytest

from micromap_mapforge.fetch import download
from micromap_mapforge.fetch._providers import RemoteFile, get_json, select_and_download
from micromap_mapforge.fetch.types import FetchError

DATA = b"tax_id\n562\n"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    monkeypatch.setattr(download, "_resolve_ips", lambda host: ["93.184.216.34"])


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_get_json_returns_parsed(tmp_path):
    client = _client(lambda req: httpx.Response(200, json={"a": 1}))
    assert get_json(client, "https://example.org/x", what="thing") == {"a": 1}


def test_get_json_non_json_raises(tmp_path):
    client = _client(lambda req: httpx.Response(200, content=b"<html>"))
    with pytest.raises(FetchError, match="not valid JSON"):
        get_json(client, "https://example.org/x", what="thing")


def test_select_first_with_inventory_note(tmp_path):
    files = [RemoteFile("a.csv", len(DATA), "https://example.org/a"),
             RemoteFile("b.csv", 5, "https://example.org/b")]
    client = _client(lambda req: httpx.Response(200, content=DATA))
    dest, sha, note = select_and_download(files, file=None, dest_dir=tmp_path,
                                          client=client, what="Demo deposit")
    assert dest == tmp_path / "a.csv"
    assert sha == hashlib.sha256(DATA).hexdigest()
    assert "2 files" in note and "--file" in note


def test_select_named_no_note(tmp_path):
    files = [RemoteFile("a.csv", len(DATA), "https://example.org/a"),
             RemoteFile("b.csv", len(DATA), "https://example.org/b")]
    client = _client(lambda req: httpx.Response(200, content=DATA))
    dest, _sha, note = select_and_download(files, file="b.csv", dest_dir=tmp_path,
                                           client=client, what="Demo deposit")
    assert dest == tmp_path / "b.csv"
    assert note == ""


def test_select_unknown_file_raises(tmp_path):
    files = [RemoteFile("a.csv", 1, "https://example.org/a")]
    client = _client(lambda req: httpx.Response(200, content=DATA))
    with pytest.raises(FetchError, match="no file 'nope.csv'"):
        select_and_download(files, file="nope.csv", dest_dir=tmp_path,
                            client=client, what="Demo deposit")


def test_select_no_files_raises(tmp_path):
    client = _client(lambda req: httpx.Response(200, content=DATA))
    with pytest.raises(FetchError, match="no files"):
        select_and_download([], file=None, dest_dir=tmp_path, client=client, what="Demo deposit")


def test_select_oversize_raises(tmp_path):
    files = [RemoteFile("huge.bin", 10**12, "https://example.org/h")]
    client = _client(lambda req: httpx.Response(200, content=DATA))
    with pytest.raises(FetchError, match="exceeds"):
        select_and_download(files, file=None, dest_dir=tmp_path, client=client, what="Demo deposit")


def test_select_skips_redownload(tmp_path):
    (tmp_path / "a.csv").write_bytes(DATA)
    files = [RemoteFile("a.csv", len(DATA), "https://example.org/a")]

    def handler(req):
        raise AssertionError("should not download when file already present")

    _dest, sha, _note = select_and_download(files, file="a.csv", dest_dir=tmp_path,
                                            client=_client(handler), what="Demo deposit")
    assert sha == hashlib.sha256(DATA).hexdigest()


def test_get_json_guards_private_host(monkeypatch):
    # get_json must SSRF-guard the URL it fetches (C3b feeds it API-returned
    # URLs). A host resolving to a private IP is refused, not fetched.
    monkeypatch.setattr(download, "_resolve_ips",
                        lambda host: ["10.0.0.1"] if "evil" in host else ["93.184.216.34"])
    client = _client(lambda req: httpx.Response(200, json={"ok": True}))
    with pytest.raises(FetchError, match="private/link-local"):
        get_json(client, "https://evil.internal/x", what="thing")


def test_get_json_guards_redirect_hop(monkeypatch):
    # A 30x to a private host is caught on the redirect hop, not followed.
    monkeypatch.setattr(download, "_resolve_ips",
                        lambda host: ["10.0.0.1"] if "evil" in host else ["93.184.216.34"])

    def handler(req):
        return httpx.Response(302, headers={"location": "https://evil.internal/x"})

    with pytest.raises(FetchError, match="private/link-local"):
        get_json(_client(handler), "https://api.example.org/x", what="thing")
