"""Offline BioCypher adapter runner.

A BioCypher adapter in this project follows a deliberately small contract:

  class MyAdapter:
      name: str                        # human label
      schema_config: dict              # Biolink / LinkML schema_config
      def get_nodes(self) -> Iterable[
          tuple[str, str, dict, dict, str | None, str | None]
      ]: ...
      def get_edges(self) -> Iterable[
          tuple[str, str, str, dict, dict, str | None, str | None]
      ]: ...

Node tuple: (id, label, properties, provenance, confidence_str | None, tier | None)
Edge tuple: (type, from_id, to_id, properties, provenance, confidence_str | None, tier | None)

This shape is BioCypher-compatible (it matches their adapter examples) without
importing biocypher in our test path — keeping the runner unit tests dependency-free.
"""

from __future__ import annotations

import hashlib
import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol

from micromap_mapforge.confidence import Confidence
from micromap_mapforge.integration.biocypher.ir import (
    ContributionBundle,
    IREdge,
    IRNode,
    SourceRef,
)


class AdapterLoadError(ImportError):
    """Raised when load_adapter cannot resolve a dotted path."""


class AdapterProtocol(Protocol):
    name: str
    schema_config: dict[str, Any]

    def get_nodes(self) -> Iterable[tuple]: ...
    def get_edges(self) -> Iterable[tuple]: ...


@dataclass
class OfflineAdapter:
    """Materialized snapshot of an adapter run, ready to convert to IR."""

    name: str
    schema_config: dict[str, Any]
    nodes: list[tuple] = field(default_factory=list)
    edges: list[tuple] = field(default_factory=list)


def load_adapter(dotted: str) -> type:
    """Resolve `package.module:ClassName` → class object.

    Raises AdapterLoadError for any failure (module missing, attribute missing).
    """
    if ":" not in dotted:
        raise AdapterLoadError(
            f"adapter path must be 'module:ClassName'; got {dotted!r}"
        )
    mod_name, attr = dotted.split(":", 1)
    try:
        mod = importlib.import_module(mod_name)
    except ImportError as exc:
        raise AdapterLoadError(f"cannot import {mod_name!r}: {exc}") from exc
    try:
        return getattr(mod, attr)
    except AttributeError as exc:
        raise AdapterLoadError(
            f"module {mod_name!r} has no attribute {attr!r}"
        ) from exc


def run_adapter(adapter: AdapterProtocol) -> OfflineAdapter:
    """Drain the adapter into a materialized OfflineAdapter snapshot."""
    return OfflineAdapter(
        name=adapter.name,
        schema_config=dict(adapter.schema_config),
        nodes=list(adapter.get_nodes()),
        edges=list(adapter.get_edges()),
    )


def _coerce_confidence(value: str | None) -> Confidence | None:
    if value is None:
        return None
    try:
        return Confidence(value)
    except ValueError:
        raise ValueError(
            f"confidence {value!r} is not a valid Confidence member "
            f"(expected one of {[m.value for m in Confidence]})"
        ) from None


def build_contribution_bundle(
    offline: OfflineAdapter,
    *,
    organization_id: str,
    source: SourceRef,
    schema_version: str = "1.0",
) -> ContributionBundle:
    """Convert an OfflineAdapter snapshot into a validated ContributionBundle."""
    nodes: list[IRNode] = []
    for i, tup in enumerate(offline.nodes):
        try:
            node_id, label, props, prov, conf, tier = tup
        except ValueError as exc:
            raise ValueError(
                f"Adapter {offline.name!r} node[{i}] tuple has wrong arity "
                f"(got {len(tup)} elements). "
                f"Contract: (id, label, properties, provenance, confidence, tier)"
            ) from exc
        nodes.append(IRNode(
            label=label,
            id=node_id,
            properties=dict(props),
            provenance=dict(prov),
            confidence=_coerce_confidence(conf),
            tier=tier,
        ))
    edges: list[IREdge] = []
    for i, tup in enumerate(offline.edges):
        try:
            e_type, f_id, t_id, props, prov, conf, tier = tup
        except ValueError as exc:
            raise ValueError(
                f"Adapter {offline.name!r} edge[{i}] tuple has wrong arity "
                f"(got {len(tup)} elements). "
                f"Contract: (type, from_id, to_id, properties, provenance, confidence, tier)"
            ) from exc
        edges.append(IREdge(
            type=e_type,
            from_id=f_id,
            to_id=t_id,
            properties=dict(props),
            provenance=dict(prov),
            confidence=_coerce_confidence(conf),
            tier=tier,
        ))
    return ContributionBundle(
        schema_version=schema_version,
        schema_config=offline.schema_config,
        organization_id=organization_id,
        nodes=nodes,
        edges=edges,
        source=source,
    )


_ARCHIVE_SUFFIXES = (".tar", ".tar.gz", ".tgz", ".zip", ".tar.bz2", ".tar.xz")


def _is_archive(p: Path) -> bool:
    name = p.name.lower()
    return any(name.endswith(sfx) for sfx in _ARCHIVE_SUFFIXES)


_HASH_CHUNK_SIZE = 1 << 20  # 1 MiB


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(_HASH_CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_tree(d: Path) -> str:
    """Stable tree digest: for each file under `d` (sorted by POSIX relative path),
    hash 'relpath:digest\\n'. Sort happens AFTER POSIX normalization so the
    digest is reproducible across Windows / Linux / macOS for the same tree.
    Sub-directories included via their files only."""
    entries: list[tuple[str, str]] = []
    for child in d.rglob("*"):
        if child.is_file():
            rel = child.relative_to(d).as_posix()
            entries.append((rel, _sha256_file(child)))
    h = hashlib.sha256()
    for rel, digest in sorted(entries):
        h.update(rel.encode("utf-8"))
        h.update(b":")
        h.update(digest.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def source_ref_for_path(path: str | Path) -> SourceRef:
    """Build a SourceRef from a filesystem path (file / dir / archive)."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    if p.is_dir():
        return SourceRef(kind="dir", path=str(p), sha256=_sha256_tree(p))
    if _is_archive(p):
        return SourceRef(kind="archive", archive_path=str(p), sha256=_sha256_file(p))
    return SourceRef(kind="file", path=str(p), sha256=_sha256_file(p))
