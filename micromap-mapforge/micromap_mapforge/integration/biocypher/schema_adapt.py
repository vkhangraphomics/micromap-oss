"""C7 (adapt mode, #76): remap an adapter's raw labels onto a project's
ontology classes via BioCypher's `input_label` schema_config convention.

`adopt` mode (the default) takes an adapter's own schema_config/labels
verbatim. `adapt` overlays a *project* schema_config instead — the project's
classes declare which raw adapter label(s) they correspond to via
`input_label` (a real BioCypher schema_config field, string or list — see
`biocypher._mapping`), and this module builds the reverse lookup and applies
it to an OfflineAdapter snapshot before it's converted into a
ContributionBundle.
"""
from __future__ import annotations

from typing import Any

from .runner import OfflineAdapter

__all__ = ["UnmappedLabelError", "build_input_label_map", "remap_offline_adapter"]


class UnmappedLabelError(Exception):
    """An adapter emitted a raw label that no project schema_config class
    declares via `input_label`. Adapt mode requires every emitted label to
    resolve to a project class — this fails loudly rather than letting an
    unmapped, adapter-native label leak into the bundle."""


def build_input_label_map(schema_config: dict[str, Any]) -> dict[str, str]:
    """``{raw_input_label: class_name}`` from a project schema_config's
    classes. ``input_label`` may be a single string or a list of strings
    (BioCypher convention) — every raw label a class declares maps to that
    class name. Classes without an ``input_label`` are skipped (adapt mode
    only remaps labels a class explicitly opts into).

    Raises ValueError if two classes claim the same raw label (ambiguous —
    an adapter-emitted label can only resolve to one project class).
    """
    mapping: dict[str, str] = {}
    for class_name, class_def in (schema_config.get("classes") or {}).items():
        raw = (class_def or {}).get("input_label")
        if raw is None:
            continue
        labels = [raw] if isinstance(raw, str) else list(raw)
        for label in labels:
            if label in mapping and mapping[label] != class_name:
                raise ValueError(
                    f"input_label {label!r} is claimed by both "
                    f"{mapping[label]!r} and {class_name!r} — ambiguous"
                )
            mapping[label] = class_name
    return mapping


def remap_offline_adapter(
    offline: OfflineAdapter, project_schema_config: dict[str, Any],
) -> OfflineAdapter:
    """A new OfflineAdapter with every node/edge label remapped through
    ``project_schema_config``'s input_label map, and its schema_config
    replaced with the project's (adopt mode keeps the adapter's own)."""
    label_map = build_input_label_map(project_schema_config)

    def _remap(raw_label: str) -> str:
        try:
            return label_map[raw_label]
        except KeyError:
            raise UnmappedLabelError(
                f"adapter {offline.name!r} emitted label {raw_label!r}, which "
                f"no class in the project schema_config maps via input_label "
                f"(available: {sorted(label_map)})"
            ) from None

    nodes = [
        (node_id, _remap(label), props, prov, conf, tier)
        for node_id, label, props, prov, conf, tier in offline.nodes
    ]
    edges = [
        (_remap(e_type), f_id, t_id, props, prov, conf, tier)
        for e_type, f_id, t_id, props, prov, conf, tier in offline.edges
    ]

    return OfflineAdapter(
        name=offline.name,
        schema_config=project_schema_config,
        nodes=nodes,
        edges=edges,
    )
