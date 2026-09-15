"""C3a (#76): SSRF-guarded streaming downloader."""
import hashlib

import httpx
import pytest

from micromap_mapforge.fetch import download
from micromap_mapforge.fetch.download import (
    _host_blocked,
    guard_url,
    stream_download,
)
from micromap_mapforge.fetch.types import FetchError


@pytest.mark.parametrize("ip, blocked", [
    ("127.0.0.1", True),
    ("169.254.169.254", True),   # cloud metadata
    ("10.0.0.5", True),
    ("192.168.1.1", True),
    ("172.16.0.1", True),
    ("::1", True),
    ("93.184.216.34", False),    # public
])
def test_host_blocked(ip, blocked):
    assert _host_blocked(ip) is blocked


def test_guard_url_rejects_ip_literal_loopback():
    with pytest.raises(FetchError, match="private/link-local"):
        guard_url("http://127.0.0.1/x")


def test_guard_url_rejects_hostname_resolving_to_private(monkeypatch):
    monkeypatch.setattr(download, "_resolve_ips", lambda host: ["10.0.0.1"])
    with pytest.raises(FetchError, match="private/link-local"):
        guard_url("https://sneaky.test/data.csv")


def test_guard_url_allows_public(monkeypatch):
    monkeypatch.setattr(download, "_resolve_ips", lambda host: ["93.184.216.34"])
    guard_url("https://example.org/data.csv")   # no raise


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_stream_download_writes_file_and_returns_sha(tmp_path, monkeypatch):
    monkeypatch.setattr(download, "_resolve_ips", lambda host: ["93.184.216.34"])
    payload = b"tax_id,name\n562,E. coli\n"

    def handler(req):
        return httpx.Response(200, content=payload)

    dest = tmp_path / "data.csv"
    sha = stream_download("https://example.org/data.csv", dest, client=_client(handler))
    assert dest.read_bytes() == payload
    assert sha == hashlib.sha256(payload).hexdigest()


def test_stream_download_follows_redirect(tmp_path, monkeypatch):
    monkeypatch.setattr(download, "_resolve_ips", lambda host: ["93.184.216.34"])

    def handler(req):
        if req.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://example.org/final"})
        return httpx.Response(200, content=b"ok")

    dest = tmp_path / "f"
    stream_download("https://example.org/start", dest, client=_client(handler))
    assert dest.read_bytes() == b"ok"


def test_stream_download_rejects_oversize_content_length(tmp_path, monkeypatch):
    monkeypatch.setattr(download, "_resolve_ips", lambda host: ["93.184.216.34"])

    def handler(req):
        return httpx.Response(200, headers={"content-length": "999"}, content=b"x")

    with pytest.raises(FetchError, match="exceeds"):
        stream_download("https://example.org/big", tmp_path / "f",
                        client=_client(handler), max_bytes=10)


def test_stream_download_rejects_oversize_stream(tmp_path, monkeypatch):
    monkeypatch.setattr(download, "_resolve_ips", lambda host: ["93.184.216.34"])

    def handler(req):
        # No content-length header; the cap must trip on streamed bytes.
        return httpx.Response(200, content=b"0123456789ABCDEF")

    with pytest.raises(FetchError, match="exceeds"):
        stream_download("https://example.org/big", tmp_path / "f",
                        client=_client(handler), max_bytes=8)
    assert not (tmp_path / "f").exists()   # partial file cleaned up


def test_stream_download_caps_redirects(tmp_path, monkeypatch):
    monkeypatch.setattr(download, "_resolve_ips", lambda host: ["93.184.216.34"])

    def handler(req):
        return httpx.Response(302, headers={"location": "https://example.org/again"})

    with pytest.raises(FetchError, match="redirect"):
        stream_download("https://example.org/loop", tmp_path / "f",
                        client=_client(handler))


def test_stream_download_redirect_without_location_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(download, "_resolve_ips", lambda host: ["93.184.216.34"])

    def handler(req):
        return httpx.Response(302, headers={})   # 3xx, no Location

    with pytest.raises(FetchError, match="Location"):
        stream_download("https://example.org/bad-redirect", tmp_path / "f",
                        client=_client(handler))


def test_stream_download_dns_failure_raises_fetcherror(tmp_path, monkeypatch):
    import socket

    def boom(host):
        raise socket.gaierror("name resolution failed")

    monkeypatch.setattr(download, "_resolve_ips", boom)

    def handler(req):
        return httpx.Response(200, content=b"never reached")

    with pytest.raises(FetchError, match="download failed"):
        stream_download("https://nonexistent.invalid/x", tmp_path / "f",
                        client=_client(handler))
