"""Route a remote source identifier to the right fetcher — C3a/C3b (#76)."""
from __future__ import annotations

import httpx

from .dryad import fetch_dryad
from .figshare import fetch_figshare
from .identifier import classify
from .osf import fetch_osf
from .types import FetchError, FetchResult
from .url import fetch_url
from .zenodo import fetch_zenodo

# provider name -> fetcher. Each fetcher: (identifier, *, file, dest_dir, client).
_PROVIDERS = {
    "zenodo": fetch_zenodo,
    "figshare": fetch_figshare,
    "dryad": fetch_dryad,
    "osf": fetch_osf,
}


def fetch(arg: str, *, file: str | None = None, dest_dir,
          client: httpx.Client | None = None) -> FetchResult:
    """Fetch a remote source (provider DOI/URL or direct URL) into `dest_dir`.
    Raises FetchError for unsupported identifiers or any download failure. Local
    paths are not fetchable — the CLI handles those directly."""
    c = classify(arg)
    if c.kind == "provider":
        fetcher = _PROVIDERS.get(c.provider)
        if fetcher is None:
            raise FetchError(
                f"provider {c.provider!r} is classified but has no registered "
                "fetcher (add it to _PROVIDERS in dispatch.py)"
            )
        return fetcher(c.id, file=file, dest_dir=dest_dir, client=client)
    if c.kind == "url":
        return fetch_url(c.href, dest_dir=dest_dir, client=client)
    if c.kind == "unsupported_doi":
        raise FetchError(
            "this DOI is not from a fetchable provider (Zenodo, Figshare, Dryad, "
            "OSF); generic DOI resolution is not yet supported"
        )
    if c.kind == "unsupported_wb":
        raise FetchError(
            "Workbench references are resolved by micromap-mcp, not MapForge "
            "— pass a local path."
        )
    raise FetchError(f"{arg!r} is a local path, not a fetchable identifier")
