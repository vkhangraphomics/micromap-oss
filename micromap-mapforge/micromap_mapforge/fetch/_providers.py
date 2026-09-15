"""Shared helpers for repository-provider fetchers — C3b (#76).

A provider module hits its API, normalizes the file listing into a list of
``RemoteFile``, and hands it to ``select_and_download`` — which picks the first
or ``--file``-named file, rejects oversize before downloading, skips a
re-download when the file is already present, streams via the SSRF-guarded
``download.stream_download``, and builds the multi-file inventory note. This is
the C3a Zenodo logic, extracted so all four providers share one copy.
"""
from __future__ import annotations

import datetime
import hashlib
from dataclasses import dataclass
from pathlib import Path

import httpx

from . import download
from .types import FetchError


@dataclass
class RemoteFile:
    name: str
    size: int | None      # bytes, or None if the API omits it
    url: str              # direct download URL


def today() -> str:
    return datetime.date.today().isoformat()


def get_json(client: httpx.Client, url: str, *, what: str) -> dict:
    """GET + SSRF-guarded manual redirects + guarded JSON parse.

    C3b providers feed API-returned URLs into this (OSF's ``links.next``, Dryad's
    ``version_href`` joined onto the base host), so — like the download path —
    every hop is checked with ``download.guard_url`` before the request, and
    redirects are followed manually so a 30x to a private/link-local host can't
    slip past. A non-JSON body is wrapped as FetchError (some hosts serve HTML
    200s)."""
    current = url
    for _ in range(download._MAX_REDIRECTS + 1):
        download.guard_url(current)
        resp = client.get(current, follow_redirects=False)
        if resp.has_redirect_location:
            current = str(httpx.URL(current).join(resp.headers["location"]))
            continue
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError as e:
            raise FetchError(f"{what}: response was not valid JSON ({e})") from e
    raise FetchError(f"{what}: too many redirects")


def _inventory_note(files: list[RemoteFile], chosen: str, what: str) -> str:
    parts = "; ".join(f"{f.name} ({f.size if f.size is not None else '?'}B)" for f in files)
    return (f"{what} has {len(files)} files [{parts}]; fetched {chosen!r}. "
            f"Pick another with `--file <name>`.")


def select_and_download(files: list[RemoteFile], *, file: str | None, dest_dir: str | Path,
                        client: httpx.Client, what: str) -> tuple[Path, str, str]:
    """Pick + download one file. Returns (local_path, sha256, note)."""
    if not files:
        raise FetchError(f"{what} has no files")

    if file is not None:
        chosen = next((f for f in files if f.name == file), None)
        if chosen is None:
            raise FetchError(
                f"{what} has no file {file!r}. Files: {[f.name for f in files]}"
            )
    else:
        chosen = files[0]

    if chosen.size is not None and chosen.size > download._MAX_FETCH_BYTES:
        raise FetchError(
            f"{chosen.name} is {chosen.size} bytes; exceeds the "
            f"{download._MAX_FETCH_BYTES}-byte fetch limit"
        )

    dest = Path(dest_dir) / chosen.name
    if dest.exists() and chosen.size is not None and dest.stat().st_size == chosen.size:
        sha = hashlib.sha256(dest.read_bytes()).hexdigest()
    else:
        sha = download.stream_download(chosen.url, dest, client=client)

    note = ""
    if file is None and len(files) > 1:
        note = _inventory_note(files, chosen.name, what)
    return dest, sha, note
