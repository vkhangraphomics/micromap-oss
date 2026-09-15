"""Streaming download with an SSRF guard, redirect + size caps — C3a (#76).

The host resolver (`_resolve_ips`) and the client factory (`_default_client`)
are module-level so tests monkeypatch them; nothing here touches real DNS or
the network under test.
"""
from __future__ import annotations

import hashlib
import ipaddress
import socket
from pathlib import Path

import httpx

from .types import FetchError

# Mirror inspect/archive.py::_MAX_TOTAL_BYTES — one ceiling for fetched and
# expanded payloads alike.
_MAX_FETCH_BYTES = 500 * 1024 * 1024   # 500 MB
_MAX_REDIRECTS = 5
_CHUNK = 64 * 1024


def _host_blocked(ip: str) -> bool:
    """True if the IP is loopback / private / link-local / reserved."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True   # un-parseable -> refuse
    return (addr.is_loopback or addr.is_private or addr.is_link_local
            or addr.is_reserved or addr.is_multicast or addr.is_unspecified)


def _resolve_ips(host: str) -> list[str]:
    """Resolve a hostname to its IP strings. If `host` is already an IP
    literal, return it as-is. Real DNS; monkeypatched in tests."""
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    infos = socket.getaddrinfo(host, None)
    return [info[4][0] for info in infos]


def guard_url(url: str) -> None:
    """Raise FetchError if the URL's host resolves to a non-public address."""
    host = httpx.URL(url).host
    if not host:
        raise FetchError(f"no host in URL: {url}")
    for ip in _resolve_ips(host):
        if _host_blocked(ip):
            raise FetchError(
                f"refusing to fetch from a private/link-local address ({host} -> {ip})"
            )


def _default_client() -> httpx.Client:
    return httpx.Client(timeout=30.0)


def stream_download(href: str, dest: Path, *, client: httpx.Client | None = None,
                    max_bytes: int = _MAX_FETCH_BYTES) -> str:
    """Download `href` to `dest`, returning the hex sha256. Follows redirects
    manually (guarding each hop), caps total bytes, cleans up a partial file on
    failure. Raises FetchError on a blocked host, oversize body, or redirect loop.
    """
    own = client is None
    client = client or _default_client()
    dest = Path(dest)
    try:
        url = href
        for _ in range(_MAX_REDIRECTS + 1):
            guard_url(url)
            with client.stream("GET", url, follow_redirects=False) as resp:
                if resp.has_redirect_location:
                    url = str(httpx.URL(url).join(resp.headers["location"]))
                    continue
                if resp.is_redirect:
                    raise FetchError(f"{href}: redirect response without a Location header")
                resp.raise_for_status()
                clen = resp.headers.get("content-length")
                if clen is not None and clen.isdigit() and int(clen) > max_bytes:
                    raise FetchError(
                        f"{href}: declared size {clen} exceeds the {max_bytes}-byte fetch limit"
                    )
                digest = hashlib.sha256()
                total = 0
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with dest.open("wb") as f:
                        for chunk in resp.iter_bytes(_CHUNK):
                            total += len(chunk)
                            if total > max_bytes:
                                raise FetchError(
                                    f"{href}: body exceeds the {max_bytes}-byte fetch limit"
                                )
                            digest.update(chunk)
                            f.write(chunk)
                except BaseException:
                    # Remove the partial file even on KeyboardInterrupt/SystemExit.
                    dest.unlink(missing_ok=True)
                    raise
                return digest.hexdigest()
        raise FetchError(f"{href}: too many redirects (>{_MAX_REDIRECTS})")
    except (httpx.HTTPError, OSError) as e:
        raise FetchError(f"{href}: download failed ({e})") from e
    finally:
        if own:
            client.close()
