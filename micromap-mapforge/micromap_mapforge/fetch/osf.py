"""Fetch a single file from a public OSF node — C3b (#76).

GET /v2/nodes/<guid>/files/osfstorage/ lists files with cursor pagination via
top-level `links.next`; this module follows `next` to assemble the full listing
before selecting. Response field names (`data[].attributes.name/size`,
`data[].links.download`, `links.next`) are pinned by tests/fetch/test_osf.py;
verify against the live API at smoke time. Only `kind == "file"` entries count
(folders are skipped).
"""
from __future__ import annotations

from pathlib import Path

import httpx

from . import download
from ._providers import RemoteFile, get_json, select_and_download, today
from .types import FetchError, FetchResult

_API = "https://api.osf.io/v2/nodes"
_MAX_PAGES = 100   # backstop against a pagination loop


def _files_from_pages(first_url: str, client: httpx.Client, what: str) -> list[RemoteFile]:
    out: list[RemoteFile] = []
    url = first_url
    for _ in range(_MAX_PAGES):
        payload = get_json(client, url, what=what)
        for e in (payload.get("data") or []):
            attrs = e.get("attributes") or {}
            if attrs.get("kind") != "file":
                continue
            name = attrs.get("name")
            dl = (e.get("links") or {}).get("download")
            if name and dl:
                out.append(RemoteFile(name=name, size=attrs.get("size"), url=dl))
        url = (payload.get("links") or {}).get("next")
        if not url:
            break
    return out


def fetch_osf(identifier: str, *, file: str | None = None, dest_dir: str | Path,
              client: httpx.Client | None = None) -> FetchResult:
    own = client is None
    client = client or download._default_client()
    try:
        what = f"OSF node {identifier}"
        files = _files_from_pages(
            f"{_API}/{identifier}/files/osfstorage/", client, what
        )
        dest, sha, note = select_and_download(
            files, file=file, dest_dir=dest_dir, client=client, what=what
        )
        return FetchResult(
            local_path=dest, sha256=sha,
            url=f"https://osf.io/{identifier}/",
            doi=f"10.17605/OSF.IO/{identifier}",
            accessed_at=today(), source_name=identifier, note=note,
        )
    except httpx.HTTPError as e:
        raise FetchError(f"OSF node {identifier}: request failed ({e})") from e
    finally:
        if own:
            client.close()
