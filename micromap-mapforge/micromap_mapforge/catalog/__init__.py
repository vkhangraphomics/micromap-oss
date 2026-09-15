"""Curated catalog of open-source biomedical knowledge graphs (C6, #76).

The MapForge-unique value over BioCypher's adapter ecosystem: a per-KG metadata
layer — **license + commercial-use posture**, adapter availability, a Biolink
schema preview, size, and recommended uses — so a customer can pick a KG to
bootstrap from with eyes open (and, later, have imports license-gated: C6.1).

The data lives in ``kg_sources.yaml``. Licenses/commercial flags are best-effort
as of each entry's ``last_validated`` date and **must be re-verified against the
source's current terms before commercial use** — see the file's ``_caveat``.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

_CATALOG_PATH = Path(__file__).parent / "kg_sources.yaml"

COMMERCIAL_USE = {"allowed", "restricted", "prohibited", "unknown"}
ADAPTER_STATUS = {"upstream", "contributed", "needs_contribution", "unknown", "native"}


class CatalogError(Exception):
    """The shipped catalog is malformed (bad enum, missing field, dup id)."""


@dataclass(frozen=True)
class KgSource:
    id: str
    name: str
    homepage: str
    description: str
    license: str                      # SPDX id where one applies, else a label
    commercial_use: str               # allowed | restricted | prohibited | unknown
    license_notes: str
    adapter_status: str               # upstream | contributed | needs_contribution | unknown | native
    adapter_location: str             # where the BioCypher adapter lives (or "")
    biolink_categories: list[str]     # schema preview
    recommended_for: list[str]
    est_size: str
    last_validated: str               # ISO date the license/adapter were checked
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_REQUIRED = (
    "id", "name", "homepage", "description", "license", "commercial_use",
    "license_notes", "adapter_status", "adapter_location", "biolink_categories",
    "recommended_for", "est_size", "last_validated",
)


def _build(entry: dict[str, Any]) -> KgSource:
    missing = [k for k in _REQUIRED if k not in entry]
    if missing:
        raise CatalogError(f"catalog entry {entry.get('id', '?')!r} missing fields: {missing}")
    if entry["commercial_use"] not in COMMERCIAL_USE:
        raise CatalogError(
            f"{entry['id']}: commercial_use {entry['commercial_use']!r} not in {sorted(COMMERCIAL_USE)}"
        )
    if entry["adapter_status"] not in ADAPTER_STATUS:
        raise CatalogError(
            f"{entry['id']}: adapter_status {entry['adapter_status']!r} not in {sorted(ADAPTER_STATUS)}"
        )
    known = {f for f in _REQUIRED} | {"notes"}
    extra = {k: v for k, v in entry.items() if k not in known}
    return KgSource(
        id=entry["id"], name=entry["name"], homepage=entry["homepage"],
        description=entry["description"], license=entry["license"],
        commercial_use=entry["commercial_use"], license_notes=entry["license_notes"],
        adapter_status=entry["adapter_status"], adapter_location=entry["adapter_location"],
        biolink_categories=list(entry["biolink_categories"]),
        recommended_for=list(entry["recommended_for"]),
        est_size=entry["est_size"], last_validated=entry["last_validated"],
        notes=entry.get("notes", ""), extra=extra,
    )


def load_catalog(path: str | Path | None = None) -> list[KgSource]:
    """Load + validate the KG catalog (sorted by id). Raises CatalogError on
    malformed data or duplicate ids."""
    payload = yaml.safe_load(Path(path or _CATALOG_PATH).read_text(encoding="utf-8")) or {}
    sources = [_build(e) for e in payload.get("sources", [])]
    ids = [s.id for s in sources]
    dups = {i for i in ids if ids.count(i) > 1}
    if dups:
        raise CatalogError(f"duplicate catalog ids: {sorted(dups)}")
    return sorted(sources, key=lambda s: s.id)


def list_source_ids() -> list[str]:
    return [s.id for s in load_catalog()]


def get_source(source_id: str) -> KgSource | None:
    return next((s for s in load_catalog() if s.id == source_id), None)


def catalog_caveat() -> str:
    payload = yaml.safe_load(_CATALOG_PATH.read_text(encoding="utf-8")) or {}
    return str(payload.get("_caveat", ""))
