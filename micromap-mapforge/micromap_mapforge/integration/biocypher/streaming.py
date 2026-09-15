"""Streaming adapter -> bundle path (#374).

`stream_adapter_to_bundle()` goes directly from a BioCypher adapter's
get_nodes()/get_edges() generators to a finished bundle directory, in memory
bounded by the number of distinct node labels / edge types, PLUS a
`label_index` that is O(total node count) but holds only small
(id, label, merge_field) tuples -- not full property payloads. For PrimeKG's
real ~129k nodes that's on the order of a few MB (see the design spec's
"deliberately small" framing); an adapter with millions of nodes would grow
this proportionally, so the bound is not purely a function of distinct
labels/types. See docs/superpowers/specs/2026-09-12-biocypher-streaming-import-design.md
for the full design and why this is a new function rather than a rewrite of
the existing run_adapter/build_contribution_bundle/serialize_bundle chain.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, TextIO

import yaml

from micromap_mapforge.integration.biocypher.ir import IREdge, IRNode, SourceRef
from micromap_mapforge.integration.biocypher.runner import _coerce_confidence
from micromap_mapforge.integration.biocypher.serialize import (
    _cypher_identifier,
    _edge_props_payload,
    _node_props_payload,
    _safe_filename,
    _write_manifest,
    _write_routing_yaml,
    _write_warnings,
)


class _JsonArrayWriter:
    """Write a JSON array incrementally: one Python object at a time, no
    intermediate list or full-string materialization. Always compact (no
    indent, no sort_keys) -- these files are machine-read/re-parsed, never
    asserted on for formatting."""

    def __init__(self, fp: TextIO) -> None:
        self._fp = fp
        self._first = True
        self._fp.write("[")

    def write_item(self, obj: Any) -> None:
        if not self._first:
            self._fp.write(",")
        self._first = False
        json.dump(obj, self._fp, default=str)

    def close(self) -> None:
        self._fp.write("]")


@dataclass
class _LabelWriter:
    """Lazily-opened per-label cypher/params file pair. Every node reachable
    through stream_adapter_to_bundle() has merge_field='id', existing=False
    unconditionally -- build_contribution_bundle() never passes either
    keyword when constructing an IRNode from an adapter tuple, so both
    always take their dataclass defaults. One bucket, always -- see the
    design doc's Pass 1 section for why this isn't just an observed
    convention but a structural guarantee of the AdapterProtocol tuple
    contract. That's what makes a single pass (and a single cypher template
    per label) provably correct here."""

    label: str
    cypher_dir: Path
    organization_id: str
    property_keys: set = field(default_factory=set)
    _fp: Any = None
    _array: "_JsonArrayWriter | None" = None
    safe_label: str = ""
    filename_warning: "str | None" = None

    def __post_init__(self) -> None:
        self.safe_label, was_changed = _safe_filename(self.label)
        if was_changed:
            self.filename_warning = (
                f"node label `{self.label}` contained filename-unsafe characters; "
                f"written as `nodes_{self.safe_label}.cypher` (Cypher MERGE preserves the original label)"
            )

    def write(self, node_id: str, props: dict) -> None:
        if self._fp is None:
            self._open()
        self._array.write_item({"merge_value": node_id, "props": props})

    def _open(self) -> None:
        cypher_label = _cypher_identifier(self.label)
        cy = (
            f"// Generated Cypher — {self.label} nodes "
            f"(parameterized; see nodes_{self.safe_label}.params.json)\n\n"
            f"UNWIND $batch_id AS row\n"
            f"MERGE (n:{cypher_label} {{id: row.merge_value, organization_id: $organization_id}})\n"
            "SET n += row.props;\n"
        )
        (self.cypher_dir / f"nodes_{self.safe_label}.cypher").write_text(cy, encoding="utf-8")
        self._fp = (self.cypher_dir / f"nodes_{self.safe_label}.params.json").open(
            "w", encoding="utf-8"
        )
        self._fp.write('{"organization_id": ')
        json.dump(self.organization_id, self._fp)
        self._fp.write(', "batch_id": ')
        self._array = _JsonArrayWriter(self._fp)

    def close(self) -> None:
        if self._fp is not None:
            self._array.close()
            self._fp.write("}")
            self._fp.close()


def _stream_nodes(
    adapter,
    cypher_dir: Path,
    organization_id: str,
    ir_nodes_writer: _JsonArrayWriter,
) -> tuple[dict[str, tuple[str, str]], list[dict], list[str], int]:
    """Single pass over adapter.get_nodes(). Returns (label_index,
    entities_block, warnings, total_node_count). See _LabelWriter's
    docstring for why one pass is provably sufficient here. total_node_count
    counts every processed tuple (matching the original chain's
    len(bundle.nodes) exactly) -- deliberately not len(label_index), which
    would silently undercount a duplicate id."""
    warnings: list[str] = []
    label_index: dict[str, tuple[str, str]] = {}
    writers: dict[str, _LabelWriter] = {}
    total_nodes = 0

    for i, tup in enumerate(adapter.get_nodes()):
        try:
            node_id, label, props, prov, conf, tier = tup
        except ValueError as exc:
            # getattr, not adapter.name -- AdapterProtocol.name is not
            # guaranteed present on every object passed here (e.g. a raw
            # test double), and this error message shouldn't itself crash
            # with AttributeError instead of reporting the real problem.
            adapter_name = getattr(adapter, "name", type(adapter).__name__)
            raise ValueError(
                f"Adapter {adapter_name!r} node[{i}] tuple has wrong arity "
                f"(got {len(tup)} elements). "
                f"Contract: (id, label, properties, provenance, confidence, tier)"
            ) from exc

        node = IRNode(
            label=label,
            id=node_id,
            properties=dict(props),
            provenance=dict(prov),
            confidence=_coerce_confidence(conf),
            tier=tier,
        )
        # First-seen wins on a duplicate node id, matching serialize.py's
        # _build_label_index() (#374 final review Fix 8).
        label_index.setdefault(node_id, (label, node.merge_field))

        writer = writers.get(label)
        if writer is None:
            writer = _LabelWriter(label=label, cypher_dir=cypher_dir, organization_id=organization_id)
            writers[label] = writer
        # Track the raw IR properties (not the Neo4j-payload projection, which
        # adds provenance_source/confidence/tier) so entities_block's columns
        # match _entities_block()'s semantics exactly (serialize.py:139-142).
        writer.property_keys.update(node.properties.keys())
        writer.write(node_id, _node_props_payload(node))

        ir_dict = {k: v for k, v in asdict(node).items() if v is not None}
        ir_nodes_writer.write_item(ir_dict)
        total_nodes += 1

    for writer in writers.values():
        writer.close()
        if writer.filename_warning is not None:
            warnings.append(writer.filename_warning)

    entities = []
    for label in sorted(writers):
        writer = writers[label]
        columns = {"id": "id"}
        for prop in sorted(writer.property_keys):
            if prop != "id":
                columns[prop] = prop
        entities.append({"label": label, "match_on": "id", "columns": columns})

    return label_index, entities, warnings, total_nodes


@dataclass
class _TypeAccumulator:
    """Pass 2a's per-type summary: bounded by distinct endpoint label/
    merge_field pairs, never by row count."""

    from_endpoints: set = field(default_factory=set)
    to_endpoints: set = field(default_factory=set)
    property_keys: set = field(default_factory=set)


def _validate_edge_tuple(adapter, i: int, tup: tuple) -> tuple[str, str, str, dict, dict, Any, Any]:
    try:
        e_type, f_id, t_id, props, prov, conf, tier = tup
    except ValueError as exc:
        # getattr, not adapter.name -- same rationale as _stream_nodes above:
        # AdapterProtocol.name is not guaranteed present on every object
        # passed here (e.g. a raw test double), and this error message
        # shouldn't itself crash with AttributeError instead of reporting
        # the real problem.
        adapter_name = getattr(adapter, "name", type(adapter).__name__)
        raise ValueError(
            f"Adapter {adapter_name!r} edge[{i}] tuple has wrong arity "
            f"(got {len(tup)} elements). "
            f"Contract: (type, from_id, to_id, properties, provenance, confidence, tier)"
        ) from exc
    return e_type, f_id, t_id, props, prov, conf, tier


def _accumulate_edge_types(
    adapter, label_index: dict[str, tuple[str, str]]
) -> tuple[dict[str, _TypeAccumulator], list[str]]:
    """Pass 2a: resolve endpoints, accumulate per-type shape. No row data kept."""
    acc: dict[str, _TypeAccumulator] = {}
    warnings: list[str] = []
    for i, tup in enumerate(adapter.get_edges()):
        e_type, f_id, t_id, props, _prov, _conf, _tier = _validate_edge_tuple(adapter, i, tup)
        state = acc.setdefault(e_type, _TypeAccumulator())
        f_ep = label_index.get(f_id)
        t_ep = label_index.get(t_id)
        if f_ep is None:
            warnings.append(
                f"{e_type}: from_id={f_id} not present in bundle node table — "
                f"emitting label-less MATCH (spike F3 cross-batch case)"
            )
        if t_ep is None:
            warnings.append(
                f"{e_type}: to_id={t_id} not present in bundle node table — "
                f"emitting label-less MATCH (spike F3 cross-batch case)"
            )
        state.from_endpoints.add(f_ep)
        state.to_endpoints.add(t_ep)
        state.property_keys.update(dict(props).keys())
    return acc, warnings


def _resolve_match_clauses(
    acc: dict[str, _TypeAccumulator],
) -> dict[str, tuple[str, str, str, tuple[str, str, str, str]]]:
    """Per type: (a_match, b_match, batch_name, (f_label, f_mf, t_label, t_mf)),
    matching today's _write_rel_cypher resolution exactly. The fourth element
    is the resolved endpoint (label, merge_field) pair for each side -- the
    single source of truth for the labeled-vs-unlabeled decision, consumed
    both for the Cypher MATCH clauses (a_match/b_match) and for the
    mapping.yaml relationships block's "from"/"to" strings, so the two never
    disagree (#374 final review Fix 3)."""
    resolved: dict[str, tuple[str, str, str, tuple[str, str, str, str]]] = {}
    for e_type, state in acc.items():
        resolved_f = [x for x in state.from_endpoints if x is not None]
        resolved_t = [x for x in state.to_endpoints if x is not None]
        if len(resolved_f) == 1 and None not in state.from_endpoints:
            f_label, f_mf = resolved_f[0]
            a_match = f"(a:{_cypher_identifier(f_label)} {{{f_mf}: row.from}})"
        else:
            f_label, f_mf = "CROSS_BATCH", "id"
            a_match = "(a {id: row.from})"
        if len(resolved_t) == 1 and None not in state.to_endpoints:
            t_label, t_mf = resolved_t[0]
            b_match = f"(b:{_cypher_identifier(t_label)} {{{t_mf}: row.to}})"
        else:
            t_label, t_mf = "CROSS_BATCH", "id"
            b_match = "(b {id: row.to})"
        resolved[e_type] = (a_match, b_match, f"batch_{f_mf}__{t_mf}", (f_label, f_mf, t_label, t_mf))
    return resolved


def _stream_edges(
    adapter,
    cypher_dir: Path,
    organization_id: str,
    label_index: dict[str, tuple[str, str]],
    ir_edges_writer: _JsonArrayWriter,
) -> tuple[list[dict], list[str], int]:
    """Two passes over adapter.get_edges() -- see the design doc's Pass 2
    section for why one pass isn't enough here (unlike nodes). Returns
    (relationships_block, warnings, total_edge_count) -- the count is
    tracked here, during the write pass, rather than by re-reading the
    just-written params.json files afterward (which would reintroduce the
    exact "read a huge file into memory just to count things" problem this
    plan exists to fix)."""
    acc, warnings = _accumulate_edge_types(adapter, label_index)
    match_clauses = _resolve_match_clauses(acc)

    files: dict[str, tuple[Any, _JsonArrayWriter]] = {}
    for e_type, (a_match, b_match, batch_name, _endpoints) in match_clauses.items():
        safe_type, was_changed = _safe_filename(e_type)
        if was_changed:
            warnings.append(
                f"relationship type `{e_type}` contained filename-unsafe characters; "
                f"written as `rels_{safe_type}.cypher` (Cypher MERGE preserves the original type)"
            )
        cy = (
            f"// Generated Cypher — {e_type} relationships "
            f"(parameterized; see rels_{safe_type}.params.json)\n\n"
            f"UNWIND ${batch_name} AS row\n"
            f"MATCH {a_match},\n"
            f"      {b_match}\n"
            f"MERGE (a)-[r:{_cypher_identifier(e_type)} {{organization_id: $organization_id}}]->(b)\n"
            "SET r += row.props;\n"
        )
        (cypher_dir / f"rels_{safe_type}.cypher").write_text(cy, encoding="utf-8")
        fp = (cypher_dir / f"rels_{safe_type}.params.json").open("w", encoding="utf-8")
        fp.write('{"organization_id": ')
        json.dump(organization_id, fp)
        fp.write(f', "{batch_name}": ')
        files[e_type] = (fp, _JsonArrayWriter(fp))

    total_edges = 0
    for i, tup in enumerate(adapter.get_edges()):
        e_type, f_id, t_id, props, prov, conf, tier = _validate_edge_tuple(adapter, i, tup)
        edge = IREdge(
            type=e_type, from_id=f_id, to_id=t_id,
            properties=dict(props), provenance=dict(prov),
            confidence=_coerce_confidence(conf), tier=tier,
        )
        if e_type not in files:
            raise ValueError(
                f"Adapter {getattr(adapter, 'name', type(adapter).__name__)!r} yielded edge type "
                f"{e_type!r} on the second get_edges() pass that wasn't seen on the first — "
                f"get_edges() must yield the same set of edge types on every call."
            )
        _fp, array = files[e_type]
        array.write_item({"from": f_id, "to": t_id, "props": _edge_props_payload(edge)})
        ir_dict = {k: v for k, v in asdict(edge).items() if v is not None}
        ir_edges_writer.write_item(ir_dict)
        total_edges += 1

    for fp, array in files.values():
        array.close()
        fp.write("}")
        fp.close()

    relationships = []
    for e_type in match_clauses:
        _a_match, _b_match, _batch_name, (f_label, f_mf, t_label, t_mf) = match_clauses[e_type]
        relationships.append({
            "type": e_type,
            "from": f"{f_label}({f_mf}=row.from)",
            "to": f"{t_label}({t_mf}=row.to)",
            "properties": {k: k for k in sorted(acc[e_type].property_keys)},
        })

    return relationships, warnings, total_edges


@dataclass
class StreamStats:
    nodes_written: int
    edges_written: int
    labels: list[str]
    edge_types: list[str]


def stream_adapter_to_bundle(
    adapter,
    out_dir,
    *,
    organization_id: str,
    source: SourceRef,
    schema_version: str = "1.0",
) -> StreamStats:
    """Adapter -> bundle directory, memory bounded by distinct labels/types
    plus a `label_index` that is O(total node count) (small (id, label,
    merge_field) tuples only, no property payloads -- see the module
    docstring for why this is small in practice but not a fixed bound).

    --schema-mode=adopt only. Produces the same files as
    run_adapter() -> build_contribution_bundle() -> serialize_bundle() for
    adopt-mode bundles; see the golden-equivalence test in Task 7.
    """
    root = Path(out_dir)
    (root / "cypher").mkdir(parents=True, exist_ok=True)

    # Stream bundle.ir.json to a temp file OUTSIDE root, so _write_manifest's
    # scan over root never sees it -- matches serialize.py's sync-path comment
    # ("written AFTER the manifest so it stays an untracked sidecar") and the
    # design spec's Pass-2 ordering ("...then the manifest (must stay last,
    # per the existing contract), then close the bundle.ir.json writer").
    # Moved into place only after the manifest step. Still genuinely
    # streaming: nothing here buffers the full content in memory.
    ir_tmp_fd, ir_tmp_path_str = tempfile.mkstemp(suffix=".bundle.ir.json.tmp")
    ir_tmp_path = Path(ir_tmp_path_str)
    try:
        with os.fdopen(ir_tmp_fd, "w", encoding="utf-8") as ir_fp:
            ir_fp.write('{"schema_version": ')
            json.dump(schema_version, ir_fp)
            ir_fp.write(', "schema_config": ')
            json.dump(adapter.schema_config, ir_fp, default=str)
            ir_fp.write(', "organization_id": ')
            json.dump(organization_id, ir_fp)
            ir_fp.write(', "nodes": ')
            ir_nodes_writer = _JsonArrayWriter(ir_fp)

            label_index, entities, node_warnings, total_nodes = _stream_nodes(
                adapter, root / "cypher", organization_id, ir_nodes_writer
            )
            ir_nodes_writer.close()

            # Zero-node parity with serialize.py:89-94 -- fail loudly rather
            # than write a broken `entities: []` bundle and exit 0 (#374
            # final review Fix 2). Only when there's no pre-existing
            # mapping.yaml to fall back to (serialize.py's own empty-bundle
            # tolerance, for a bundle directory being re-populated in place,
            # doesn't apply here since we haven't written past the IR temp
            # file yet).
            if total_nodes == 0 and not (root / "mapping.yaml").exists():
                raise ValueError(
                    "Cannot serialize a ContributionBundle with no nodes — "
                    "mapping.yaml requires at least one entity (entities[*].minItems == 1)"
                )

            ir_fp.write(', "edges": ')
            ir_edges_writer = _JsonArrayWriter(ir_fp)

            relationships, edge_warnings, total_edges = _stream_edges(
                adapter, root / "cypher", organization_id, label_index, ir_edges_writer
            )
            ir_edges_writer.close()

            source_dict = {k: v for k, v in asdict(source).items() if v is not None}
            ir_fp.write(', "source": ')
            json.dump(source_dict, ir_fp, default=str)
            ir_fp.write("}")
        # `with` block closed ir_fp already; the temp file persists on disk
        # (mkstemp doesn't auto-delete) until moved or removed below.

        mapping = {
            "source": {
                "name": adapter.schema_config.get("name", "schema-adapter-source"),
                "format": "schema_adapter",
                "path": source.archive_path if source.kind == "archive" else (source.path or ""),
                "description":
                    "schema_adapter — bundle produced by a schema-carrying IR adapter; "
                    "provenance and structure live in the bundle itself.",
            },
            "entities": entities,
            "relationships": relationships,
        }
        (root / "mapping.yaml").write_text(yaml.safe_dump(mapping, sort_keys=False), encoding="utf-8")
        # Unconditional, unlike serialize_bundle()'s `if not routing_existed:`
        # guard -- and NOT because "import-kg always creates a fresh bundle
        # directory" (mkdir(..., exist_ok=True) above makes re-running into
        # the same --out directory legal). It's safe for a different, real
        # reason: serialize_bundle()'s guard exists to protect a
        # policy-derived routing.yaml carrying `provenance.enabled` from a
        # prior `mapforge plan` step on the tabular ingestion path -- that
        # key never applies to an import-kg output directory (no workflow
        # writes it there), and _write_routing_yaml itself calls plan_route
        # with no policy either way, so overwriting is a no-op in substance.
        _write_routing_yaml(organization_id, root)

        warnings = node_warnings + edge_warnings
        _write_warnings(root, warnings)
        _write_manifest(root)  # does NOT see bundle.ir.json yet -- matches the sync path

        shutil.move(str(ir_tmp_path), str(root / "bundle.ir.json"))
    except Exception:
        ir_tmp_path.unlink(missing_ok=True)
        raise

    return StreamStats(
        nodes_written=total_nodes,
        edges_written=total_edges,
        labels=sorted({label for label, _mf in label_index.values()}),
        edge_types=sorted({r["type"] for r in relationships}),
    )
