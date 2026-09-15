"""Direct single-file URL fetch — C3a (#76).

The filename is the URL's last path segment (falling back to ``download``).
Content-Disposition-based naming is a deferred refinement.
"""
from __future__ import annotations

import datetime
from pathlib import Path

import httpx

from . import download
from .types import FetchResult


def _filename_from_url(href: str) -> str:
    segment = httpx.URL(href).path.rsplit("/", 1)[-1]
    return segment or "download"


def fetch_url(href: str, *, dest_dir, client: httpx.Client | None = None) -> FetchResult:
    name = _filename_from_url(href)
    dest = Path(dest_dir) / name
    sha = download.stream_download(href, dest, client=client)
    return FetchResult(
        local_path=dest,
        sha256=sha,
        url=href,
        doi=None,
        accessed_at=datetime.date.today().isoformat(),
        source_name=Path(name).stem,
        note="",
    )
