"""Build per-node-type resolvers by preloading Neo4j lookup indexes once."""

import logging
from typing import Any, Callable

from ..mapping.schema_config import (
    default_schema_config,
    load_ontology_from_schema_config,
)
from ..normalize import normalize_disease_name
from .base import EntityResolver
from .disease import DiseaseResolver
from .fuzzy import FuzzyResolver
from .generic import GenericResolver
from .normalizers import get_normalizer
from .paper import PaperResolver

logger = logging.getLogger(__name__)


def _ontology_dict_from_schema_config(schema_config: dict[str, Any] | None) -> dict[str, Any]:
    """Convert a schema_config dict to the legacy {nodes, relationships}
    shape that ``_projection_fields`` and ``build_resolvers`` were written
    against (pre-A1' slice 3). ``schema_config=None`` falls back to the
    package default. Centralizing the conversion keeps the legacy dict
    shape isolated to this module — A1' slice 4 retires it entirely."""
    cfg = schema_config if schema_config is not None else default_schema_config()
    ont = load_ontology_from_schema_config(cfg)
    return {"nodes": ont.nodes, "relationships": ont.relationships}


def _projection_fields(label: str, ontology: dict[str, Any]) -> list[str]:
    """Identifier fields + name_field — everything the resolver lookup
    indexes touch. Anything else on the node is irrelevant to resolution
    and must NOT be pulled across the wire (GH#70)."""
    node_def = ontology["nodes"][label]
    fields = list(dict.fromkeys(node_def.get("identifiers", [])))
    name_field = node_def.get("name_field")
    if name_field and name_field not in fields:
        fields.append(name_field)
    return fields


def _fetch_nodes(driver, label: str, database: str,
                 schema_config: dict[str, Any] | None = None,
                 organization_id: str | None = None) -> list[dict[str, Any]]:
    """Fetch lookup-relevant properties for every node of `label`.

    Projects only the identifier fields and name_field declared in the
    project's schema_config (A1' slice 3, #74) — never `properties(n)`,
    which is unbounded and OOMs against a populated MicroMap (GH#70).
    Streams the cursor instead of materializing the full result set in
    driver memory.

    ``schema_config=None`` falls back to the package-baked default so
    pre-slice-3 callers keep working.

    ``organization_id`` (#314): when set, the preload carries an org
    predicate — the caller's org plus :func:`canonical_orgs` — so a tenant
    can only resolve onto their own nodes and shared reference data. The org
    is bound as a parameter, never interpolated. ``None`` keeps the
    unscoped preload for the standalone CLI / operator path.
    """
    fields = _projection_fields(label, _ontology_dict_from_schema_config(schema_config))
    projection = ", ".join(f"n.{f} AS {f}" for f in fields)
    cypher = f"MATCH (n:{label}) RETURN {projection}"
    params: dict[str, Any] = {}
    if organization_id is not None:
        from ..provenance.spine import canonical_orgs
        cypher = (
            f"MATCH (n:{label}) "
            "WHERE n.organization_id = $org OR n.organization_id IN $canonical_orgs "
            f"RETURN {projection}"
        )
        params = {
            "org": organization_id,
            "canonical_orgs": sorted(canonical_orgs()),
        }
    with driver.session(database=database) as session:
        # Unscoped preloads keep the one-arg call so pre-#314 test fakes
        # (and any driver shim) that only accept the query string still work.
        result = session.run(cypher, params) if params else session.run(cypher)
        # Stream: never call .data() (which materializes the full list).
        return [
            {f: record[f] for f in fields if record.get(f) is not None}
            for record in result
        ]


def _build_identifier_index(
    nodes: list[dict[str, Any]],
    identifier_fields: list[str],
) -> dict[str, dict[str, dict[str, Any]]]:
    index: dict[str, dict[str, dict[str, Any]]] = {f: {} for f in identifier_fields}
    for props in nodes:
        for field in identifier_fields:
            v = props.get(field)
            if v is not None:
                index[field][str(v)] = props
    return index


def _build_name_index(
    nodes: list[dict[str, Any]],
    name_field: str | None,
) -> dict[str, list[dict[str, Any]]]:
    if not name_field:
        return {}
    index: dict[str, list[dict[str, Any]]] = {}
    for props in nodes:
        v = props.get(name_field)
        if v is None:
            continue
        key = str(v).strip().lower()
        index.setdefault(key, []).append(props)
    return index


def _build_normalized_name_index(
    nodes: list[dict[str, Any]],
    name_field: str | None,
    normalizer: Callable[[str], str] | None,
) -> dict[str, list[dict[str, Any]]]:
    """Name index keyed by the normalized name. Generalizes
    _build_disease_name_index: when `normalizer` is None the key is the raw
    lowercased/stripped name (same keying as _build_name_index)."""
    index: dict[str, list[dict[str, Any]]] = {}
    if not name_field:
        return index
    norm = normalizer or (lambda s: s.strip().lower())
    for props in nodes:
        v = props.get(name_field)
        if v is None:
            continue
        key = norm(str(v))
        if not key:
            continue
        index.setdefault(key, []).append(props)
    return index


def _build_disease_name_index(nodes: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for props in nodes:
        name = props.get("name")
        if name is None:
            continue
        normalized = normalize_disease_name(str(name))
        if not normalized:
            continue
        index.setdefault(normalized, []).append(props)
    return index


def _build_generic(label, node_def, nodes, id_index):
    name_field = node_def.get("name_field")
    return GenericResolver(
        label=label,
        identifier_fields=list(node_def.get("identifiers", [])),
        name_field=name_field,
        primary_id_field=node_def["primary_id"],
        identifier_index=id_index,
        name_index=_build_name_index(nodes, name_field),
    )


def _build_disease(label, node_def, nodes, id_index):
    return DiseaseResolver(
        identifier_fields=list(node_def.get("identifiers", [])),
        primary_id_field=node_def["primary_id"],
        identifier_index=id_index,
        name_index_normalized=_build_disease_name_index(nodes),
    )


def _build_paper(label, node_def, nodes, id_index):
    name_field = node_def.get("name_field")
    return PaperResolver(
        identifier_fields=list(node_def.get("identifiers", [])),
        primary_id_field=node_def["primary_id"],
        identifier_index=id_index,
        title_index=_build_name_index(nodes, name_field),
    )


def _build_fuzzy(label, node_def, nodes, id_index):
    name_field = node_def.get("name_field")
    normalizer = get_normalizer(node_def.get("normalizer"))
    resolver_cfg = node_def.get("resolver") or {}
    threshold = resolver_cfg.get("threshold", 0.85)
    return FuzzyResolver(
        label=label,
        name_field=name_field,
        identifier_fields=list(node_def.get("identifiers", [])),
        primary_id_field=node_def["primary_id"],
        identifier_index=id_index,
        name_index_normalized=_build_normalized_name_index(nodes, name_field, normalizer),
        normalizer=normalizer,
        fuzzy_threshold=threshold,
    )


STRATEGY_REGISTRY = {
    "generic": _build_generic,
    "fuzzy": _build_fuzzy,
    "disease": _build_disease,
    "paper": _build_paper,
}


def _strategy_for(node_def: dict) -> str:
    """The resolver strategy for a node: explicit x_mapforge.resolver.strategy
    wins; otherwise infer `fuzzy` when a normalizer is declared (back-compat for
    legacy configs without a resolver block), else `generic`."""
    cfg = node_def.get("resolver") or {}
    strategy = cfg.get("strategy")
    if strategy:
        return strategy
    return "fuzzy" if node_def.get("normalizer") else "generic"


def build_resolvers(
    driver,
    database: str = "neo4j",
    labels_of_interest: set[str] | None = None,
    schema_config: dict[str, Any] | None = None,
    organization_id: str | None = None,
) -> dict[str, EntityResolver]:
    """Build one resolver per project node type, preloading indexes from Neo4j.

    Args:
        driver: an open Neo4j driver.
        database: the Neo4j database name.
        labels_of_interest: when provided, the preload skips any project
            label not in that set. This is the fix for #117 — bundles
            whose mapping only references ``Taxon``/``Disease``/``Paper``
            no longer issue ``MATCH (n:Gene)``-style queries that emit
            ~25 ``01N42`` notifications per resolve run.
        schema_config: a LinkML + ``x_mapforge`` project schema (see
            ``mapping/schema_config.py``). ``None`` (the default) loads
            the package-baked microbiome default — same effect as the
            pre-A1' direct read of ``ontology.yaml`` (Theme A1' slice 3,
            #74, part of #135).
        organization_id: when set (#314), every preload query is scoped to
            this org plus :func:`canonical_orgs` — a tenant resolves
            against their own nodes and shared reference data only, and
            cannot attach edges onto (or probe the existence of) another
            org's nodes. ``None`` preserves the unscoped preload for the
            standalone CLI / operator path; the MCP surface always passes
            the authenticated principal's org.

    The label set is intersected with the schema_config's classes — any
    label in ``labels_of_interest`` but not in the schema_config is
    silently dropped. Classes with ``is_a: association`` are relationships,
    not node labels, and don't get a resolver. Passing ``labels_of_interest=None``
    preserves the pre-#117 behavior: every project node label is preloaded.
    """
    ontology = _ontology_dict_from_schema_config(schema_config)
    resolvers: dict[str, EntityResolver] = {}

    for label, node_def in ontology["nodes"].items():
        if labels_of_interest is not None and label not in labels_of_interest:
            continue
        identifier_fields = list(node_def.get("identifiers", []))
        primary_id_field = node_def.get("primary_id")
        if primary_id_field is None:
            # A schema_config class without an x_mapforge.primary_id can't
            # be resolved against (there's no merge key). Skip silently.
            continue
        nodes = _fetch_nodes(driver, label, database, schema_config=schema_config,
                             organization_id=organization_id)
        id_index = _build_identifier_index(nodes, identifier_fields)

        strategy = _strategy_for(node_def)
        factory = STRATEGY_REGISTRY.get(strategy)
        if factory is None:
            logger.warning(
                "unknown resolver strategy %r for label %s; using generic",
                strategy, label,
            )
            factory = _build_generic
        resolvers[label] = factory(label, node_def, nodes, id_index)

    return resolvers
