"""Build a Contribution Bundle IR from the tabular ingestion pipeline's
(mapping, rows, resolution-report, organization_id, source_ref) tuple.

Mirrors the row-level decisions in ``micromap_mapforge.emit.cypher`` so that
``serialize_bundle(tabular_to_ir(...), out_dir)`` produces the same
``mapping.yaml`` + ``cypher/*`` artifacts the legacy ``generate_cypher`` path
produces today, but routed through the 3a IR + 3b-0 discriminator so the
tabular path benefits from uniform provenance handling and a single schema
surface (issue #97, sub-issue #125).

Per-row decision algorithm (entity):

  1. Look up the source column for ``entity.match_on`` (fall back to first column
     if missing — matches legacy at ``emit/cypher.py:76``).
  2. Skip rows whose source-column value is ``None`` or empty (``emit/cypher.py:82``).
  3. Look up ``(label, str(term))`` in the resolution report. If a best
     candidate exists with a ``merge_field`` and ``merge_value``, the IRNode is
     ``existing=True`` with the resolver's ``merge_field``/``merge_value``;
     otherwise the IRNode is ``existing=False`` with ``merge_field = entity.match_on``
     and ``merge_value = term`` (``emit/cypher.py:84-90``).
  4. Build ``properties``: every column-mapped property except the merge field
     and the source column itself; ``None`` row values are skipped
     (``emit/cypher.py:91-100``). The legacy emitter's ``source_origin:'contributor'``
     tag is NOT replicated here — provenance is the IR's responsibility, and
     3b-2 (#126) will land structured provenance/confidence.

Dedup: rows producing the same (label, IRNode.id) collapse to one IRNode.
Last-write-wins on properties (matches resolver semantics where (label, term)
is the resolution key).

Relationship handling reproduces what the legacy ``emit/cypher.py`` did before it
was retired (#127) — this is now the sole site for that logic. Each endpoint's
MATCH key is derived from the resolution report (resolved →
``(merge_field, merge_value)``) or the mapping's endpoint expression (fall-through
→ ``(endpoint.field, term)``). The IREdge's ``from_id``/``to_id`` are the
merge_value of each endpoint, which the serializer's ``_build_label_index``
looks up against ``IRNode.id`` to recover the endpoint's label and ``merge_field``
for the MATCH clause. Cross-batch endpoints (the edge's endpoint isn't a node
in this bundle) fall through to the serializer's CROSS_BATCH path.
"""

from __future__ import annotations

import re
from typing import Any

from micromap_mapforge.confidence import Confidence
from micromap_mapforge.integration.biocypher.ir import (
    ContributionBundle,
    IREdge,
    IRNode,
    SourceRef,
)
from micromap_mapforge.resolve.pipeline import ResolutionReport


# Greedy `.+` anchored to the FINAL `)` so a nested expr (e.g.
# `Disease(doid=norm(row.x))`) is captured whole, not truncated at the first
# inner `)` as the old `[^)]+` did (#79 F4). `_parse_endpoint` strips the
# endpoint first, so the trailing `\s*$` only guards stray whitespace.
_ENDPOINT_RE = re.compile(r"(?P<label>\w+)\((?P<field>\w+)=(?P<expr>.+)\)\s*$")


# 3b-2 (#126): map mapping-schema confidence values to the IR's open-provenance
# method vocabulary. The IR has a closed `method` enum ("mined"|"curated"|
# "literature") while confidence stays its own enum (EXTRACTED/INFERRED/AMBIGUOUS).
# Rationale:
#   EXTRACTED → 'curated' — the source explicitly states this fact.
#   INFERRED  → 'mined'   — the source was processed to infer this.
#   AMBIGUOUS → 'mined'   — still automated; multiple candidates rejected.
# "literature" is reserved for paper-derived sources (Theme C) where the
# mapping itself encodes that the source is a literature corpus; the tabular
# path doesn't infer literature from confidence alone.
_CONFIDENCE_TO_METHOD: dict[Confidence, str] = {
    Confidence.EXTRACTED: "curated",
    Confidence.INFERRED: "mined",
    Confidence.AMBIGUOUS: "mined",
}


def tabular_to_ir(
    mapping: dict[str, Any],
    rows: list[dict[str, Any]],
    report: ResolutionReport,
    organization_id: str,
    source_ref: SourceRef,
    schema_config: dict[str, Any] | None = None,
) -> ContributionBundle:
    """Build a ContributionBundle from the tabular pipeline's intermediate state.

    Args:
        mapping: parsed ``mapping.yaml`` dict.
        rows: full source-row list (NOT the inspector's sample — issue #123).
        report: ResolutionReport from ``resolve.pipeline.resolve_mapping``.
        organization_id: routing-derived org id (from ``routing.yaml``).
        source_ref: SourceRef describing the input file/dir/archive.
        schema_config: optional schema_config dict to embed in the bundle.
            Defaults to a placeholder annotating the source name so that
            ``serialize._entities_block`` can produce a usable mapping.yaml.

    Returns:
        A ContributionBundle ready for ``serialize_bundle()``.
    """
    resolved_index = _index_resolved(report)
    source = mapping.get("source") or {}
    source_name = source.get("name", "tabular-source")
    ref_column: str | None = source.get("ref_column")

    nodes: list[IRNode] = []
    seen_node_ids: set[tuple[str, str]] = set()  # (label, id) for dedup

    for entity in mapping.get("entities", []):
        label = entity["label"]
        match_on = entity["match_on"]
        columns = entity["columns"]
        source_column = columns.get(match_on) or next(iter(columns.values()))
        entity_confidence = _confidence_or_none(entity.get("confidence"))

        for row in rows:
            term = row.get(source_column)
            if term is None or term == "":
                continue
            resolved_pair = resolved_index.get((label, str(term)))
            if resolved_pair is not None:
                merge_field, merge_value = resolved_pair
                existing = True
            else:
                merge_field, merge_value = match_on, str(term)
                existing = False

            props: dict[str, Any] = {}
            for ont_field, src_col in columns.items():
                if ont_field == merge_field or row.get(src_col) is None:
                    continue
                # On fall-through (new node) write ALL mapped display columns so the
                # node carries fields like Disease.name even when match_on is a
                # derived key (e.g. name_normalized) where source_column IS the name
                # column. When resolving to an existing canonical node, keep the
                # conservative behavior of not re-writing the source-term column to
                # avoid clobbering canonical values.
                if existing and src_col == source_column:
                    continue
                props[ont_field] = row[src_col]

            node_provenance = _build_provenance(source_name, entity_confidence, ref_column, row)

            key = (label, merge_value)
            if key in seen_node_ids:
                # Last-write-wins on properties: locate and update the existing
                # IRNode rather than appending a duplicate.
                for n in nodes:
                    if n.label == label and n.id == merge_value:
                        n.properties.update(props)
                        break
                continue
            seen_node_ids.add(key)
            nodes.append(IRNode(
                label=label,
                id=merge_value,
                properties=props,
                provenance=node_provenance,
                confidence=entity_confidence,
                merge_field=merge_field,
                existing=existing,
            ))

    edges: list[IREdge] = []
    seen_edge_keys: set[tuple[str, str, str]] = set()  # (type, from_id, to_id)

    for rel in mapping.get("relationships", []):
        rel_type = rel["type"]
        from_expr = _parse_endpoint(rel["from"])
        to_expr = _parse_endpoint(rel["to"])
        rel_props_def = rel.get("properties", {})
        rel_confidence = _confidence_or_none(rel.get("confidence"))

        for row in rows:
            from_pair = _endpoint_merge_pair(from_expr, row, resolved_index)
            to_pair = _endpoint_merge_pair(to_expr, row, resolved_index)
            if from_pair is None or to_pair is None:
                continue
            _, from_value = from_pair
            _, to_value = to_pair

            rel_props: dict[str, Any] = {}
            for k, v in rel_props_def.items():
                if isinstance(v, dict) and "constant" in v:
                    rel_props[k] = v["constant"]
                elif isinstance(v, str) and row.get(v) is not None:
                    rel_props[k] = row[v]

            edge_provenance = _build_provenance(source_name, rel_confidence, ref_column, row)

            edge_key = (rel_type, from_value, to_value)
            if edge_key in seen_edge_keys:
                continue
            seen_edge_keys.add(edge_key)
            edges.append(IREdge(
                type=rel_type,
                from_id=from_value,
                to_id=to_value,
                properties=rel_props,
                provenance=edge_provenance,
                confidence=rel_confidence,
            ))

    return ContributionBundle(
        schema_version="1.0",
        schema_config=schema_config or {"name": source_name, "source": "tabular"},
        organization_id=organization_id,
        nodes=nodes,
        edges=edges,
        source=source_ref,
    )


# ---------------------------------------------------------------------------
# Helpers — direct lifts from emit/cypher.py so the per-row decision logic
# stays in lockstep with the legacy emitter.
# ---------------------------------------------------------------------------


def _confidence_or_none(raw: Any) -> Confidence | None:
    """Coerce a mapping's confidence value to the Confidence enum, or None
    if the mapping doesn't declare one. Strict: an unknown string raises so
    a typo in the mapping surfaces at IR-build time, not at apply time."""
    if raw is None:
        return None
    if isinstance(raw, Confidence):
        return raw
    return Confidence(raw)  # raises ValueError on unknown strings — desired


def _build_provenance(
    source_name: str,
    confidence: Confidence | None,
    ref_column: str | None,
    row: dict[str, Any],
) -> dict[str, Any]:
    """Assemble {source, method?, ref?} for an IRNode/IREdge.

    - `source` is always set (the IR requires it).
    - `method` is added when confidence has a known mapping in _CONFIDENCE_TO_METHOD.
    - `ref` is added when the mapping declares a `ref_column` and the row's
      value at that column is non-null/non-empty.
    """
    prov: dict[str, Any] = {"source": source_name}
    if confidence is not None and confidence in _CONFIDENCE_TO_METHOD:
        prov["method"] = _CONFIDENCE_TO_METHOD[confidence]
    if ref_column:
        ref_value = row.get(ref_column)
        if ref_value is not None and ref_value != "":
            prov["ref"] = str(ref_value)
    return prov


def _index_resolved(report: ResolutionReport) -> dict[tuple[str, str], tuple[str, str]]:
    """(label, source_term) → (merge_field, merge_value) for the best candidate.

    Direct mirror of ``emit/cypher.py:_index_resolved``.
    """
    out: dict[tuple[str, str], tuple[str, str]] = {}
    for row in report.resolved:
        best = row.best
        if best is not None and best.merge_field and best.merge_value:
            out[(row.entity_label, row.source_term)] = (best.merge_field, best.merge_value)
    return out


def _parse_endpoint(endpoint: str) -> dict[str, str]:
    """Parse 'Label(field=row.column)' into its components.

    Handles a nested expr (``Label(field=fn(row.column))``) by capturing through
    the final ``)`` — see ``_ENDPOINT_RE`` (#79 F4).
    """
    m = _ENDPOINT_RE.match(endpoint.strip())
    if not m:
        raise ValueError(f"Cannot parse endpoint expr: {endpoint!r}")
    expr = m.group("expr").strip()
    col_match = re.search(r"row\.(\w+)", expr)
    return {
        "label": m.group("label"),
        "field": m.group("field"),
        "source_column": col_match.group(1) if col_match else "",
    }


def _endpoint_merge_pair(
    endpoint: dict[str, str],
    row: dict[str, Any],
    resolved_index: dict[tuple[str, str], tuple[str, str]],
) -> tuple[str, str] | None:
    """Resolved → (merge_field, merge_value); fall-through → (endpoint.field,
    term); None if term missing. (Was mirrored in the retired emit/cypher.py, #127.)"""
    src_col = endpoint["source_column"]
    term = row.get(src_col)
    if term is None or term == "":
        return None
    pair = resolved_index.get((endpoint["label"], str(term)))
    if pair is not None:
        return pair
    return endpoint["field"], str(term)
