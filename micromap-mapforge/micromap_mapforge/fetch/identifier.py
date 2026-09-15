"""Classify a `mapforge inspect/map` source argument — C3a/C3b (#76). Pure.

Provider DOIs/URLs are matched from ordered ``(provider, regex)`` lists whose
``group(1)`` is the provider-specific identifier (Zenodo record id, Figshare
article id, the bare Dryad DOI, the OSF guid). Adding a provider = appending one
DOI pattern and one URL pattern.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# (provider, DOI regex) — group(1) is the identifier passed to that fetcher.
_PROVIDER_DOI: list[tuple[str, re.Pattern]] = [
    ("zenodo", re.compile(r"^10\.5281/zenodo\.(\d+)$", re.IGNORECASE)),
    # Figshare: 10.6084/m9.figshare.<article_id>[.v<n>] — id is the article number.
    ("figshare", re.compile(r"^10\.6084/m9\.figshare\.(\d+)(?:\.v\d+)?$", re.IGNORECASE)),
    # Dryad: 10.5061/dryad.<x> — id is the FULL DOI (Dryad's API keys on it).
    ("dryad", re.compile(r"^(10\.5061/dryad\.\S+)$", re.IGNORECASE)),
    # OSF: 10.17605/OSF.IO/<guid> — id is the guid.
    ("osf", re.compile(r"^10\.17605/OSF\.IO/(\w+)$", re.IGNORECASE)),
]
# (provider, landing-page URL regex) — group(1) is the identifier.
_PROVIDER_URL: list[tuple[str, re.Pattern]] = [
    ("zenodo", re.compile(r"^https?://zenodo\.org/records?/(\d+)\b", re.IGNORECASE)),
    ("figshare", re.compile(r"^https?://figshare\.com/articles/[^/]+/[^/]+/(\d+)\b", re.IGNORECASE)),
    ("dryad", re.compile(r"^https?://datadryad\.org/stash/dataset/doi:(10\.5061/dryad\.\S+)$", re.IGNORECASE)),
    ("osf", re.compile(r"^https?://osf\.io/(\w+)/?$", re.IGNORECASE)),
]
# Any bare DOI (matched only after the provider DOIs, so a provider DOI never
# falls through to "unsupported").
_BARE_DOI = re.compile(r"^10\.\d{4,}/\S+$", re.IGNORECASE)


@dataclass(frozen=True)
class Classification:
    kind: str                     # local | url | provider | unsupported_doi | unsupported_wb
    provider: str | None = None   # zenodo | figshare | dryad | osf  (when kind == "provider")
    id: str | None = None         # provider-specific identifier
    href: str | None = None       # direct URL (when kind == "url")


def classify(arg: str) -> Classification:
    s = arg.strip()

    # DOI forms — explicit `doi:` prefix or bare.
    doi = s[4:] if s.lower().startswith("doi:") else s
    for provider, pat in _PROVIDER_DOI:
        if m := pat.match(doi):
            return Classification("provider", provider=provider, id=m.group(1))
    if _BARE_DOI.match(doi):
        return Classification("unsupported_doi")

    # Workbench reference — resolved by micromap-mcp, not here.
    if s.lower().startswith("wb://"):
        return Classification("unsupported_wb")

    # Provider landing-page URLs route to the provider; other URLs download direct.
    for provider, pat in _PROVIDER_URL:
        if m := pat.match(s):
            return Classification("provider", provider=provider, id=m.group(1))
    if s.lower().startswith(("http://", "https://")):
        return Classification("url", href=s)

    # Anything else is a local path (validated by the CLI).
    return Classification("local")
