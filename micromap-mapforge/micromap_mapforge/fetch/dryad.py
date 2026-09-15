"""Fetch a single file from a public Dryad dataset — C3b (#76).

Two hops: GET /api/v2/datasets/<url-encoded-doi> gives the latest version link;
GET <version>/files lists files, each with a `stash:download` href. Response
field names (`_links.stash:version`, `_embedded.stash:files`, `path`) are pinned
by tests/fetch/test_dryad.py; verify against the live API at smoke time.
"""
from __future__ import annotations

import urllib.parse
from pathlib import Path

import httpx

from . import download
from ._providers import RemoteFile, get_json, select_and_download, today
from .types import FetchError, FetchResult

_BASE = "https://datadryad.org"
_API = f"{_BASE}/api/v2"


def _files_from_version(payload: dict) -> list[RemoteFile]:
    out: list[RemoteFile] = []
    for f in (payload.get("_embedded", {}).get("stash:files") or []):
        name = f.get("path")
        href = (f.get("_links", {}).get("stash:download", {}) or {}).get("href")
        if not (name and href):
            continue
        out.append(RemoteFile(name=name, size=f.get("size"), url=f"{_BASE}{href}"))
    return out


def fetch_dryad(identifier: str, *, file: str | None = None, dest_dir: str | Path,
                client: httpx.Client | None = None) -> FetchResult:
    own = client is None
    client = client or download._default_client()
    try:
        what = f"Dryad dataset {identifier}"
        # Dryad keys the dataset endpoint on the URL-encoded DOI (incl. the `doi:` prefix).
        enc = urllib.parse.quote(f"doi:{identifier}", safe="")
        ds = get_json(client, f"{_API}/datasets/{enc}", what=what)
        version_href = (ds.get("_links", {}).get("stash:version", {}) or {}).get("href")
        if not version_href:
            raise FetchError(f"{what}: response has no version link")
        ver = get_json(client, f"{_BASE}{version_href}/files", what=what)
        files = _files_from_version(ver)
        dest, sha, note = select_and_download(
            files, file=file, dest_dir=dest_dir, client=client, what=what
        )
        url = f"https://datadryad.org/stash/dataset/doi:{identifier}"
        return FetchResult(local_path=dest, sha256=sha, url=url, doi=identifier,
                           accessed_at=today(), source_name=ds.get("title"), note=note)
    except httpx.HTTPError as e:
        raise FetchError(f"Dryad dataset {identifier}: request failed ({e})") from e
    finally:
        if own:
            client.close()
