"""Contribution Bundle IR — the schema-agnostic shape produced by adapters.

See spec §5. The IR carries provenance, confidence, and the source schema_config
through the bundle so downstream consumers (and federated peers) can read it.
"""

from __future__ import annotations

import re as _re
from dataclasses import asdict, dataclass
from typing import Any, Literal, Optional

from micromap_mapforge.confidence import Confidence

_SHA256_HEX_RE = _re.compile(r"^[0-9a-f]{64}$")


# Provenance dict shape per §5: { source: str, method: "mined"|"curated"|"literature", ref?: str }
Provenance = dict[str, Any]


@dataclass
class SourceRef:
    """File/dir/archive reference with a tree-or-file sha256 (fixes spike F2)."""

    kind: Literal["file", "dir", "archive"]
    sha256: str
    path: Optional[str] = None
    archive_path: Optional[str] = None

    def __post_init__(self) -> None:
        if self.kind == "file" and not self.path:
            raise ValueError("SourceRef(kind='file') requires path")
        if self.kind == "dir" and not self.path:
            raise ValueError("SourceRef(kind='dir') requires path")
        if self.kind == "archive" and not self.archive_path:
            raise ValueError("SourceRef(kind='archive') requires archive_path")
        if not self.sha256 or not _SHA256_HEX_RE.match(self.sha256):
            raise ValueError("SourceRef.sha256 must be a 64-char lowercase hex digest")


@dataclass
class IRNode:
    label: str
    id: str
    properties: dict[str, Any]
    provenance: Provenance
    confidence: Optional[Confidence] = None
    tier: Optional[str] = None  # passthrough tag; not interpreted
    # 3b-0 discriminators (issue #129):
    #   - merge_field: which node property to MATCH/MERGE on. Default "id"
    #     preserves BioCypher behavior (every node is CURIE-keyed on `id`).
    #     The tabular path (issue #125) sets this to the resolver's native
    #     merge field (e.g. "ncbi_taxid", "mondo_id").
    #   - existing: True ⇒ the node is a shared canonical record already in
    #     the graph; the serializer emits MATCH on `merge_field` with no
    #     organization_id in the merge key (preserves PR #108's fix for #90).
    #     False ⇒ contributor data; emit MERGE org-scoped on `merge_field`.
    merge_field: str = "id"
    existing: bool = False

    def __post_init__(self) -> None:
        if not self.label:
            raise ValueError("IRNode.label is required")
        if not self.id:
            raise ValueError("IRNode.id is required")
        if not isinstance(self.provenance, dict) or "source" not in self.provenance:
            raise ValueError("IRNode.provenance must be a dict with at least 'source'")
        if not self.merge_field:
            raise ValueError("IRNode.merge_field must be a non-empty string")


@dataclass
class IREdge:
    type: str
    from_id: str
    to_id: str
    properties: dict[str, Any]
    provenance: Provenance
    confidence: Optional[Confidence] = None
    tier: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.type:
            raise ValueError("IREdge.type is required")
        if not self.from_id or not self.to_id:
            raise ValueError("IREdge.from_id and to_id are required")
        if not isinstance(self.provenance, dict) or "source" not in self.provenance:
            raise ValueError("IREdge.provenance must be a dict with at least 'source'")


@dataclass
class ContributionBundle:
    schema_version: str
    schema_config: dict[str, Any]  # Biolink OR custom LinkML; opaque to IR
    organization_id: str
    nodes: list[IRNode]
    edges: list[IREdge]
    source: SourceRef

    def __post_init__(self) -> None:
        if not self.schema_version:
            raise ValueError("ContributionBundle.schema_version is required")
        if not isinstance(self.schema_config, dict) or not self.schema_config:
            raise ValueError("ContributionBundle.schema_config must be a non-empty dict")
        if not self.organization_id:
            raise ValueError("ContributionBundle.organization_id is required")


# ---------------------------------------------------------------------------
# JSON-schema validation (Task 1.2)
# ---------------------------------------------------------------------------

import json as _json
from importlib import resources as _resources
from functools import lru_cache as _lru_cache

import jsonschema as _jsonschema


class IRValidationError(ValueError):
    """Raised when a ContributionBundle dict fails JSON-schema validation."""


@_lru_cache(maxsize=1)
def _ir_schema() -> dict[str, Any]:
    raw = (
        _resources.files("micromap_mapforge.integration.biocypher")
        .joinpath("ir.schema.json")
        .read_text(encoding="utf-8")
    )
    return _json.loads(raw)


def validate_bundle_dict(payload: dict[str, Any]) -> None:
    """Validate a bundle dict against the IR JSON schema.

    Raises IRValidationError on any failure.
    """
    try:
        _jsonschema.validate(payload, _ir_schema())
    except _jsonschema.ValidationError as exc:
        raise IRValidationError(str(exc)) from exc


def bundle_to_schema_dict(payload_or_bundle: "ContributionBundle | dict") -> dict[str, Any]:
    """Serialize a ContributionBundle to a JSON-schema-valid dict.

    Strips None-valued keys from `source` (so the schema's oneOf
    discriminator doesn't trip on path=None / archive_path=None) and from
    each node and edge (so the schema's `confidence` enum doesn't see
    null values). Symmetric stripping is intentional — `tier` would also
    serialize to null without it, even though the schema allows null for
    tier specifically.

    Note: Confidence is a str-mixin enum. dataclasses.asdict preserves
    the enum instance in the dict (it does NOT flatten to plain str), but
    json.dumps and jsonschema both treat str subclasses as strings via
    equality comparison, so no explicit .value coercion is needed.

    Accepts a ContributionBundle (calls asdict internally) or a pre-asdict'd
    dict (passes through). Idempotent.
    """
    payload = asdict(payload_or_bundle) if not isinstance(payload_or_bundle, dict) else dict(payload_or_bundle)
    payload["source"] = {k: v for k, v in payload["source"].items() if v is not None}
    payload["nodes"] = [{k: v for k, v in n.items() if v is not None} for n in payload["nodes"]]
    payload["edges"] = [{k: v for k, v in e.items() if v is not None} for e in payload["edges"]]
    return payload
