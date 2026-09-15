"""Shared types for the fetch stage — C3a (#76)."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path


class FetchError(ValueError):
    """A fetch could not be completed (bad identifier, unsupported provider,
    oversize, blocked host, HTTP/network failure). Subclasses ValueError so the
    CLI's existing ``except ValueError -> error: … ; exit 2`` handler catches it.
    """


@dataclass
class FetchResult:
    local_path: Path          # downloaded file, inside the bundle/out dir
    sha256: str               # computed during streaming download
    url: str | None           # canonical source URL  -> mapping source.url
    doi: str | None           # bare DOI of a provider source; None for a direct URL
    accessed_at: str          # ISO-8601 date, stamped at fetch time
    source_name: str | None   # record title / filename stem -> source.name default
    note: str = ""            # multi-file deposit inventory -> report note

    def to_dict(self) -> dict:
        d = asdict(self)
        d["local_path"] = self.local_path.as_posix()
        return d
