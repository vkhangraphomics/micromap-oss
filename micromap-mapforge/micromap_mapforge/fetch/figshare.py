"""Fetch a single file from a public Figshare article — C3b (#76).

GET /v2/articles/<id> returns a `files` array with direct `download_url`s — a
one-step deposit like Zenodo. Response field names are pinned by
tests/fetch/test_figshare.py; verify against the live API at smoke time.
"""
from __future__ import annotations

from pathlib import Path

import httpx

from . import download
from ._providers import RemoteFile, get_json, select_and_download, today
from .types import FetchError, FetchResult

_API = "https://api.figshare.com/v2/articles"


def _files_from_article(rec: dict) -> list[RemoteFile]:
    out: list[RemoteFile] = []
    for f in (rec.get("files") or []):
        name = f.get("name")
        url = f.get("download_url")
        if not (name and url):
            continue
        out.append(RemoteFile(name=name, size=f.get("size"), url=url))
    return out


def fetch_figshare(identifier: str, *, file: str | None = None, dest_dir: str | Path,
                   client: httpx.Client | None = None) -> FetchResult:
    own = client is None
    client = client or download._default_client()
    try:
        what = f"Figshare article {identifier}"
        rec = get_json(client, f"{_API}/{identifier}", what=what)
        files = _files_from_article(rec)
        dest, sha, note = select_and_download(
            files, file=file, dest_dir=dest_dir, client=client, what=what
        )
        doi = rec.get("doi") or f"10.6084/m9.figshare.{identifier}"
        url = rec.get("figshare_url") or f"https://doi.org/{doi}"
        return FetchResult(local_path=dest, sha256=sha, url=url, doi=doi,
                           accessed_at=today(), source_name=rec.get("title"), note=note)
    except httpx.HTTPError as e:
        raise FetchError(f"Figshare article {identifier}: request failed ({e})") from e
    finally:
        if own:
            client.close()
