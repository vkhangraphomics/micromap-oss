"""Fetch a single file from a public Zenodo deposit — C3a/C3b (#76).

Hits the Zenodo record API, normalizes ``files[]`` into RemoteFiles, and defers
selection/download to ``_providers.select_and_download``. The download-link
field (``files[].links.self``) is pinned by tests/fetch/test_zenodo.py.
"""
from __future__ import annotations

from pathlib import Path

import httpx

from . import download
from ._providers import RemoteFile, get_json, select_and_download, today
from .types import FetchError, FetchResult

_API = "https://zenodo.org/api/records"


def _files_from_record(rec: dict) -> list[RemoteFile]:
    out: list[RemoteFile] = []
    for f in (rec.get("files") or []):
        name = f.get("key")
        url = (f.get("links") or {}).get("self")
        if not (name and url):
            continue
        out.append(RemoteFile(name=name, size=f.get("size"), url=url))
    return out


def fetch_zenodo(identifier: str, *, file: str | None = None, dest_dir: str | Path,
                 client: httpx.Client | None = None) -> FetchResult:
    own = client is None
    client = client or download._default_client()
    try:
        what = f"Zenodo record {identifier}"
        rec = get_json(client, f"{_API}/{identifier}", what=what)
        files = _files_from_record(rec)
        dest, sha, note = select_and_download(
            files, file=file, dest_dir=dest_dir, client=client, what=what
        )
        doi = rec.get("doi") or f"10.5281/zenodo.{identifier}"
        url = (rec.get("links") or {}).get("html") or f"https://zenodo.org/records/{identifier}"
        title = (rec.get("metadata") or {}).get("title")
        return FetchResult(local_path=dest, sha256=sha, url=url, doi=doi,
                           accessed_at=today(), source_name=title, note=note)
    except httpx.HTTPError as e:
        raise FetchError(f"Zenodo record {identifier}: request failed ({e})") from e
    finally:
        if own:
            client.close()
