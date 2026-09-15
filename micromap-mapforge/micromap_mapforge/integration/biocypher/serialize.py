"""IR → existing bundle serializer (spec §14.2, §15.1–§15.3).

Writes mapping.yaml + cypher/* + routing.yaml. The manifest is produced
afterwards by emit.bundle.write_manifest so the existing hashing/contract
applies unchanged.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from micromap_mapforge.integration.biocypher.ir import (
    ContributionBundle,
    IREdge,
    IRNode,
    bundle_to_schema_dict,
)


_FILENAME_UNSAFE_RE = re.compile(r"[^\w\-]")
_BARE_CYPHER_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_filename(name: str) -> tuple[str, bool]:
    """Replace non-[A-Za-z0-9_-] characters with '_'. Returns (sanitized, changed?)."""
    sanitized = _FILENAME_UNSAFE_RE.sub("_", name)
    return sanitized, sanitized != name


def _cypher_identifier(name: str) -> str:
    """A node label or relationship type, safe to interpolate into Cypher.

    Adapter-sourced labels/types aren't guaranteed to be bare Cypher
    identifiers — e.g. PrimeKG's real raw `relation` value for one edge type
    is literally "off-label use" (space + hyphen); naive interpolation
    produces a syntax error (`r:OFF-LABEL` parses as subtraction). Backtick
    quoting is Neo4j's standard escape hatch (embedded backticks doubled).
    Left untouched when `name` is already a bare identifier, so the
    overwhelming common case (ASSOCIATED_WITH_DISEASE, OrganismTaxon, ...)
    stays byte-identical to before this existed.
    """
    if _BARE_CYPHER_IDENTIFIER_RE.match(name):
        return name
    return f"`{name.replace('`', '``')}`"


def serialize_bundle(
    bundle: ContributionBundle,
    out_dir: str | Path,
    *,
    include_provenance: bool = True,
) -> Path:
    """Materialize a ContributionBundle to disk in the existing bundle shape.

    Args:
        bundle: the IR to serialize.
        out_dir: target directory for the bundle artifacts.
        include_provenance: when False, skip writing `provenance_*` properties
            on node/edge Neo4j-property payloads. Confidence and tier are NOT
            considered provenance — they remain on the payload. Honors the
            `routing.yaml::provenance.enabled` opt-out (issue #63 / PR #113).
            Extended to edge-level props in 3b-2 (#126).

    Pre-existing routing.yaml: when ``out_dir/routing.yaml`` already exists
    (the tabular-emit case where ``mapforge plan`` already wrote it), we
    DO NOT regenerate it — that would silently lose fields like
    ``provenance.enabled`` which the no-policy ``plan_route`` call inside
    ``_write_routing_yaml`` doesn't repopulate (3b-3a / #127).

    Empty-bundle tolerance: when ``bundle.nodes`` is empty AND mapping.yaml
    already exists at ``out_dir``, we keep the existing mapping.yaml +
    routing.yaml, skip the cypher/ writes, and still write a manifest so
    downstream ``approve``/``submit`` work — matching the legacy
    ``generate_cypher`` tolerance for empty-entities mappings.
    Raising ValueError stays the behavior only when *both* the bundle has
    no nodes AND no pre-existing mapping.yaml (the BioCypher case).
    """
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    mapping_existed = (root / "mapping.yaml").exists()
    routing_existed = (root / "routing.yaml").exists()

    if not bundle.nodes:
        if not mapping_existed:
            raise ValueError(
                "Cannot serialize a ContributionBundle with no nodes — "
                "mapping.yaml requires at least one entity (entities[*].minItems == 1)"
            )
        _write_manifest(root)
        return root

    label_index = _build_label_index(bundle)
    _write_mapping_yaml(bundle, root, label_index)
    node_warnings = _write_node_cypher(bundle, root, include_provenance=include_provenance)
    rel_warnings = _write_rel_cypher(bundle, root, label_index, include_provenance=include_provenance)
    if not routing_existed:
        _write_routing_yaml(bundle.organization_id, root)
    _write_warnings(root, node_warnings + rel_warnings)
    _write_manifest(root)  # MUST be last — hashes everything written above
    # IR snapshot for version diffing (#76 C8) — written AFTER the manifest so
    # it stays an untracked sidecar (no effect on manifest hashes / HTTP intake).
    (root / "bundle.ir.json").write_text(
        json.dumps(bundle_to_schema_dict(bundle), indent=2, default=str, sort_keys=True),
        encoding="utf-8",
    )
    return root


def _source_path_for_mapping(bundle: ContributionBundle) -> str:
    """Pick a single string path for mapping.yaml.source.path.

    For SourceRef(kind='archive'), use archive_path. For dir/file, use path.
    """
    ref = bundle.source
    if ref.kind == "archive":
        return ref.archive_path or ""
    return ref.path or ""


def _entities_block(bundle: ContributionBundle) -> list[dict[str, Any]]:
    """One entity per distinct node label; match_on reflects the IR merge_field.

    Property columns are the union of property keys observed on nodes of that
    label, projected as `prop_name: prop_name` self-mapping so the existing
    cypher emitter has a column→prop map.

    match_on (3b-0, issue #129): comes from the IR's `merge_field`. For
    BioCypher-style bundles it remains "id" (every node defaults merge_field
    to "id"). For tabular-derived bundles it's the resolver's native field
    (e.g. "ncbi_taxid"). The first node's merge_field per label wins; mixing
    merge_fields within a label is not supported and would silently pick one.
    """
    by_label_props: dict[str, set[str]] = defaultdict(set)
    by_label_mf: dict[str, str] = {}
    for n in bundle.nodes:
        by_label_props[n.label].update(n.properties.keys())
        # First-seen wins (deterministic for resolver-driven bundles where
        # every node of a given label shares one merge_field).
        by_label_mf.setdefault(n.label, n.merge_field)

    entities: list[dict[str, Any]] = []
    for label in sorted(by_label_props):
        match_on = by_label_mf.get(label, "id")
        columns: dict[str, str] = {match_on: match_on}
        for prop in sorted(by_label_props[label]):
            if prop != match_on:
                columns[prop] = prop
        entities.append({
            "label": label,
            "match_on": match_on,
            "columns": columns,
        })
    return entities


def _relationships_block(
    bundle: ContributionBundle,
    label_index: dict[str, tuple[str, str]],
) -> list[dict[str, Any]]:
    """Populate mapping.yaml.relationships from the IR edge table (§15.3).

    Each endpoint's field expression comes from the indexed node's
    `merge_field`; cross-batch endpoints (not present in `bundle.nodes`)
    fall back to `CROSS_BATCH(id=row.X)` — `id` is the safest assumption
    for the resolved-at-apply-time case (issue #129 / 3b-0).
    """
    # One entry per distinct edge type; union of property keys; first-seen labels.
    # CROSS_BATCH sentinel: \w+-safe placeholder for endpoints not in this bundle's
    # node table (resolved at apply time). '?' was used previously but fails the
    # _ENDPOINT_RE regex (now in tabular/to_ir.py) which requires \w+ in the label slot.
    by_type: dict[str, dict[str, Any]] = {}
    for edge in bundle.edges:
        f_label, f_mf = label_index.get(edge.from_id, ("CROSS_BATCH", "id"))
        t_label, t_mf = label_index.get(edge.to_id, ("CROSS_BATCH", "id"))
        entry = by_type.setdefault(edge.type, {
            "type": edge.type,
            "from": f"{f_label}({f_mf}=row.from)",
            "to":   f"{t_label}({t_mf}=row.to)",
            "properties": {},
        })
        for k in edge.properties.keys():
            entry["properties"][k] = k  # self-map
    return list(by_type.values())


def _write_mapping_yaml(
    bundle: ContributionBundle,
    root: Path,
    label_index: dict[str, tuple[str, str]],
) -> None:
    """Serialize the IR-derived mapping dict (source, entities, relationships) to <root>/mapping.yaml."""
    mapping: dict[str, Any] = {
        "source": {
            "name": bundle.schema_config.get("name", "schema-adapter-source"),
            "format": "schema_adapter",
            "path": _source_path_for_mapping(bundle),
            "description":
                "schema_adapter — bundle produced by a schema-carrying IR adapter; "
                "provenance and structure live in the bundle itself.",
        },
        "entities": _entities_block(bundle),
        "relationships": _relationships_block(bundle, label_index),
    }
    (root / "mapping.yaml").write_text(
        yaml.safe_dump(mapping, sort_keys=False), encoding="utf-8"
    )


def _build_label_index(bundle: ContributionBundle) -> dict[str, tuple[str, str]]:
    """Map IR node id → (label, merge_field), for resolving relationship endpoint
    label/field pairs (§15.2 + 3b-0 #129). First-seen entry per id wins, mirroring
    the pre-3b-0 single-label-per-id assumption."""
    out: dict[str, tuple[str, str]] = {}
    for n in bundle.nodes:
        out.setdefault(n.id, (n.label, n.merge_field))
    return out


def _node_props_payload(node: IRNode, *, include_provenance: bool = True) -> dict[str, Any]:
    """Project IR provenance/confidence onto Neo4j-property-friendly keys.

    When `include_provenance=False` the provenance_* keys are omitted, honoring
    the routing.yaml::provenance.enabled opt-out (3b-2, #126). Confidence and
    tier remain — they're not provenance.
    """
    props: dict[str, Any] = dict(node.properties)
    props.pop("id", None)  # id is the merge key, not a prop
    if include_provenance and isinstance(node.provenance, dict):
        if "source" in node.provenance:
            props["provenance_source"] = node.provenance["source"]
        if "method" in node.provenance:
            props["provenance_method"] = node.provenance["method"]
        if "ref" in node.provenance:
            props["provenance_ref"] = node.provenance["ref"]
    if node.confidence is not None:
        props["confidence"] = node.confidence.value
    if node.tier is not None:
        props["tier"] = node.tier
    return props


def _edge_props_payload(edge: IREdge, *, include_provenance: bool = True) -> dict[str, Any]:
    """Project IR edge provenance/confidence onto Neo4j-property-friendly keys.

    `include_provenance` mirrors _node_props_payload's semantics (3b-2, #126).
    """
    props: dict[str, Any] = dict(edge.properties)
    if include_provenance and isinstance(edge.provenance, dict):
        if "source" in edge.provenance:
            props["provenance_source"] = edge.provenance["source"]
        if "method" in edge.provenance:
            props["provenance_method"] = edge.provenance["method"]
        if "ref" in edge.provenance:
            props["provenance_ref"] = edge.provenance["ref"]
    if edge.confidence is not None:
        props["confidence"] = edge.confidence.value
    if edge.tier is not None:
        props["tier"] = edge.tier
    return props


def _write_node_cypher(
    bundle: ContributionBundle,
    root: Path,
    *,
    include_provenance: bool = True,
) -> list[str]:
    """Write per-label node Cypher files. Returns a list of warning strings.

    Bucketing (3b-0, issue #129):
      Group each label's nodes by (merge_field, existing) and emit one UNWIND
      per bucket into the same `nodes_<label>.cypher` file:
        - existing=True  → MATCH (n:Label {<merge_field>: row.merge_value})
                           SET n += row.props   -- no org-scope; shared node
        - existing=False → MERGE (n:Label {<merge_field>: row.merge_value,
                                           organization_id: $organization_id})
                           SET n += row.props   -- org-scoped contributor data

    Batch-name convention (preserves PR #101 BioCypher byte-equivalence):
      - (existing=False, merge_field='id') AND it's the only bucket → `batch_id`
        (matches today's single-batch BioCypher output exactly)
      - existing=True            → `batch_<merge_field>`              (legacy emit/cypher.py)
      - existing=False, mixed    → `batch_<merge_field>__contributor` (legacy emit/cypher.py)
    """
    cypher_dir = root / "cypher"
    cypher_dir.mkdir(parents=True, exist_ok=True)

    # by_label[label] = list[(merge_field, existing, {merge_value, props})]
    by_label: dict[str, list[tuple[str, bool, dict[str, Any]]]] = defaultdict(list)
    for node in bundle.nodes:
        by_label[node.label].append((
            node.merge_field,
            node.existing,
            {"merge_value": node.id, "props": _node_props_payload(node, include_provenance=include_provenance)},
        ))

    warnings: list[str] = []
    for label, entries in by_label.items():
        cypher_label = _cypher_identifier(label)
        safe_label, was_changed = _safe_filename(label)
        if was_changed:
            warnings.append(
                f"node label `{label}` contained filename-unsafe characters; "
                f"written as `nodes_{safe_label}.cypher` (Cypher MERGE preserves the original label)"
            )

        # Bucket by (merge_field, existing).
        buckets: dict[tuple[str, bool], list[dict[str, Any]]] = defaultdict(list)
        for mf, existing, item in entries:
            buckets[(mf, existing)].append(item)

        # BioCypher byte-equivalence: single bucket of (id, False) → `batch_id`,
        # unchanged from PR #101. Any other shape uses the legacy convention.
        only_biocypher_default = list(buckets.keys()) == [("id", False)]

        params: dict[str, Any] = {"organization_id": bundle.organization_id}
        lines: list[str] = [
            f"// Generated Cypher — {label} nodes "
            f"(parameterized; see nodes_{safe_label}.params.json)",
            "",
        ]
        # Deterministic emit order: existing=True before existing=False, then
        # by merge_field name, so mixed bundles produce stable bytes.
        for (mf, existing) in sorted(buckets.keys(), key=lambda k: (not k[1], k[0])):
            items = buckets[(mf, existing)]
            if only_biocypher_default:
                batch_name = "batch_id"
            elif existing:
                batch_name = f"batch_{mf}"
            else:
                batch_name = f"batch_{mf}__contributor"
            params[batch_name] = items
            if existing:
                # MATCH; no org-scope (issue #90 / PR #108 — shared canonical node).
                lines.append(
                    f"UNWIND ${batch_name} AS row\n"
                    f"MATCH (n:{cypher_label} {{{mf}: row.merge_value}})\n"
                    "SET n += row.props;"
                )
            else:
                lines.append(
                    f"UNWIND ${batch_name} AS row\n"
                    f"MERGE (n:{cypher_label} {{{mf}: row.merge_value, organization_id: $organization_id}})\n"
                    "SET n += row.props;"
                )

        (cypher_dir / f"nodes_{safe_label}.cypher").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
        (cypher_dir / f"nodes_{safe_label}.params.json").write_text(
            json.dumps(params, indent=2, default=str), encoding="utf-8"
        )
    return warnings


def _write_rel_cypher(
    bundle: ContributionBundle,
    root: Path,
    label_index: dict[str, tuple[str, str]],
    *,
    include_provenance: bool = True,
) -> list[str]:
    """Returns a list of WARNING strings collected during emit.

    Endpoint MATCH field (3b-0, issue #129): each endpoint's MATCH key comes
    from its IRNode's merge_field via label_index. For pure-BioCypher bundles
    every endpoint has merge_field='id' so the emitted Cypher is byte-identical
    to PR #101. For tabular bundles the MATCH uses the native field
    (e.g. `{ncbi_taxid: row.from}`).

    Param batch name encodes both endpoint merge_fields: `batch_<f_mf>__<t_mf>`.
    BioCypher default stays `batch_id__id`.
    """
    cypher_dir = root / "cypher"
    cypher_dir.mkdir(parents=True, exist_ok=True)
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    from_endpoints: dict[str, set[tuple[str, str] | None]] = defaultdict(set)
    to_endpoints:   dict[str, set[tuple[str, str] | None]] = defaultdict(set)
    warnings: list[str] = []
    for edge in bundle.edges:
        f_ep = label_index.get(edge.from_id)
        t_ep = label_index.get(edge.to_id)
        if f_ep is None:
            warnings.append(
                f"{edge.type}: from_id={edge.from_id} not present in bundle node table — "
                f"emitting label-less MATCH (spike F3 cross-batch case)"
            )
        if t_ep is None:
            warnings.append(
                f"{edge.type}: to_id={edge.to_id} not present in bundle node table — "
                f"emitting label-less MATCH (spike F3 cross-batch case)"
            )
        from_endpoints[edge.type].add(f_ep)
        to_endpoints[edge.type].add(t_ep)
        by_type[edge.type].append({
            "from": edge.from_id,
            "to": edge.to_id,
            "props": _edge_props_payload(edge, include_provenance=include_provenance),
        })

    for rel_type, items in by_type.items():
        safe_rel_type, was_changed = _safe_filename(rel_type)
        if was_changed:
            warnings.append(
                f"relationship type `{rel_type}` contained filename-unsafe characters; "
                f"written as `rels_{safe_rel_type}.cypher` (Cypher MERGE preserves the original type)"
            )
        f_set = from_endpoints[rel_type]
        t_set = to_endpoints[rel_type]
        # Single endpoint shape per side ⇒ labeled MATCH on its merge_field.
        # Otherwise (mixed labels OR cross-batch endpoint present) ⇒ unlabeled
        # MATCH on `id` (the cross-batch safe default).
        resolved_f = [x for x in f_set if x is not None]
        resolved_t = [x for x in t_set if x is not None]
        if len(resolved_f) == 1 and None not in f_set:
            f_label, f_mf = resolved_f[0]
            a_match = f"(a:{_cypher_identifier(f_label)} {{{f_mf}: row.from}})"
        else:
            a_match = "(a {id: row.from})"
            f_mf = "id"
        if len(resolved_t) == 1 and None not in t_set:
            t_label, t_mf = resolved_t[0]
            b_match = f"(b:{_cypher_identifier(t_label)} {{{t_mf}: row.to}})"
        else:
            b_match = "(b {id: row.to})"
            t_mf = "id"

        batch_name = f"batch_{f_mf}__{t_mf}"
        params = {"organization_id": bundle.organization_id, batch_name: items}
        cy = (
            f"// Generated Cypher — {rel_type} relationships "
            f"(parameterized; see rels_{safe_rel_type}.params.json)\n\n"
            f"UNWIND ${batch_name} AS row\n"
            f"MATCH {a_match},\n"
            f"      {b_match}\n"
            f"MERGE (a)-[r:{_cypher_identifier(rel_type)} {{organization_id: $organization_id}}]->(b)\n"
            "SET r += row.props;\n"
        )
        (cypher_dir / f"rels_{safe_rel_type}.cypher").write_text(cy, encoding="utf-8")
        (cypher_dir / f"rels_{safe_rel_type}.params.json").write_text(
            json.dumps(params, indent=2, default=str), encoding="utf-8"
        )
    return warnings


def _write_warnings(root: Path, warnings: list[str]) -> None:
    if not warnings:
        return
    body = "# Bundle warnings\n\n" + "\n".join(f"- {w}" for w in warnings) + "\n"
    (root / "WARNINGS.md").write_text(body, encoding="utf-8")


def _write_routing_yaml(organization_id: str, root: Path) -> None:
    from micromap_mapforge.emit.routing import plan_route
    # mapping.yaml must exist for plan_route to compute a stable digest.
    mapping = yaml.safe_load((root / "mapping.yaml").read_text(encoding="utf-8"))
    routing = plan_route(mapping, organization_id=organization_id)
    (root / "routing.yaml").write_text(
        yaml.safe_dump(routing, sort_keys=False), encoding="utf-8"
    )


def _write_manifest(root: Path) -> None:
    from micromap_mapforge.emit.bundle import build_bundle, write_manifest
    write_manifest(build_bundle(root))
