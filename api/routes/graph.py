"""
Graph traversal API routes.

Provides endpoints for multi-hop graph traversal including shortest path,
all paths, and neighborhood exploration across the knowledge graph.
"""

from fastapi import APIRouter, Depends, Query
from typing import Optional, Dict, Any

from api.scoping import OrgScope, ORG_FILTER, scope_params, resolve_org_scope
from integrations.neo4j_microbiome import MicrobiomeKG, get_microbiome_kg

router = APIRouter()

# Singleton KG instance
_kg_instance: Optional[MicrobiomeKG] = None


def get_kg() -> MicrobiomeKG:
    """Get or create the MicrobiomeKG instance."""
    global _kg_instance
    if _kg_instance is None:
        _kg_instance = get_microbiome_kg()
    return _kg_instance


# Relationship types the neighborhood traversal follows. HAS_PARENT (774K
# taxonomy-hierarchy edges) and EFFECTIVE_AGAINST (276K AMR edges) are
# deliberately omitted so a 1-3 hop expansion doesn't explode. Variable-length
# patterns only accept a positive union of types; a negated expression
# (-[:!X*1..n]-) is invalid Cypher and was the cause of this endpoint's 500s.
_NEIGHBORHOOD_REL_TYPES = [
    "ASSOCIATED_WITH_DISEASE",
    "PRODUCES",
    "PARTICIPATES_IN",
    "TARGETS",
    "MENTIONED_IN",
    "LINKED_TO_DISEASE",
    "BELONGS_TO_CLASS",
    "PROCESSES",
    "SAME_AS",
]


def _neighborhood_query(max_depth: int) -> str:
    """Build the neighborhood traversal Cypher for a start node matched by id.

    Uses a positive union of traversable relationship types (see
    _NEIGHBORHOOD_REL_TYPES) within the variable-length pattern, which prunes
    the excluded hierarchy/AMR edges during expansion rather than after.
    Scopes neighbor to the caller's org (#200); intermediate path nodes are not
    individually bound so only the endpoints are filtered.
    """
    rel_union = "|".join(_NEIGHBORHOOD_REL_TYPES)
    return f"""
    MATCH (start)
    WHERE id(start) = $neo_id
    MATCH path = (start)-[:{rel_union}*1..{max_depth}]-(neighbor)
    WHERE neighbor <> start
      AND {ORG_FILTER("neighbor")}
    WITH neighbor,
         min(length(path)) AS distance,
         head(collect(type(last(relationships(path))))) AS relationship_path
    RETURN neighbor, distance, relationship_path
    ORDER BY distance, neighbor.name
    LIMIT $limit
    """


def _find_entity_query():
    """Return a Cypher query to find an entity using label-specific indexed lookups.

    Scopes each sub-MATCH to the caller's org (#200).  Callers must merge
    scope_params(scope) into the params dict alongside their ``$id`` value.
    """
    # Uses UNION ALL with label-specific matches to leverage indexes instead of full scan
    org = ORG_FILTER("n")
    return f"""
    CALL {{
        MATCH (n:Taxon) WHERE (n.taxon_id = $id OR n.name = $id) AND {org} RETURN n LIMIT 1
        UNION ALL
        MATCH (n:Disease) WHERE (n.disease_id = $id OR n.name = $id OR toLower(n.name) = toLower($id)) AND {org} RETURN n LIMIT 1
        UNION ALL
        MATCH (n:Compound) WHERE (n.compound_id = $id OR n.metabolite_id = $id OR n.name = $id OR toLower(n.name) = toLower($id)) AND {org} RETURN n LIMIT 1
        UNION ALL
        MATCH (n:Drug) WHERE (n.drug_id = $id OR n.name = $id) AND {org} RETURN n LIMIT 1
        UNION ALL
        MATCH (n:Gene) WHERE (n.gene_id = $id OR n.name = $id) AND {org} RETURN n LIMIT 1
        UNION ALL
        MATCH (n:Pathway) WHERE (n.pathway_id = $id OR n.name = $id) AND {org} RETURN n LIMIT 1
        UNION ALL
        MATCH (n:Paper) WHERE (n.paper_id = $id OR n.name = $id) AND {org} RETURN n LIMIT 1
    }}
    RETURN n LIMIT 1
    """


def _extract_node_info(node: Any) -> Dict[str, Any]:
    """Extract a summary dict from a Neo4j node."""
    if node is None:
        return {}
    props = dict(node) if hasattr(node, "__iter__") else {}
    node_id = (
        props.get("taxon_id")
        or props.get("disease_id")
        or props.get("compound_id")
        or props.get("metabolite_id")
        or props.get("drug_id")
        or props.get("gene_id")
        or props.get("pathway_id")
        or props.get("paper_id")
        or props.get("name")
        or ""
    )
    labels = list(node.labels) if hasattr(node, "labels") else []
    return {
        "id": node_id,
        "name": props.get("name", ""),
        "type": labels[0] if labels else "Unknown",
    }


@router.get("/graph/path")
async def find_shortest_path(
    from_id: str = Query(..., description="ID or name of the starting entity"),
    to_id: str = Query(..., description="ID or name of the target entity"),
    max_depth: int = Query(3, ge=1, le=5, description="Maximum traversal depth (1-5)"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Find the shortest path between two entities in the knowledge graph.

    Searches across all entity types (Taxon, Disease, Metabolite, Drug, Gene, Pathway, Paper)
    and returns the shortest undirected path connecting them.

    **Examples:**
    - Find path between a taxon and a disease: `/graph/path?from_id=Akkermansia muciniphila&to_id=obesity`
    - Find path between two metabolites: `/graph/path?from_id=butyrate&to_id=propionate`

    Returns `found: false` with an empty path if no connection exists within the given depth.
    """
    kg = get_kg()

    # Look up both entities
    from_results = kg.execute_cypher(
        _find_entity_query().replace("$id", "$from_id"),
        {"from_id": from_id, **scope_params(scope)},
    )
    to_results = kg.execute_cypher(
        _find_entity_query().replace("$id", "$to_id"),
        {"to_id": to_id, **scope_params(scope)},
    )

    from_node = from_results[0].get("n") if from_results else None
    to_node = to_results[0].get("n") if to_results else None

    if from_node is None or to_node is None:
        return {
            "from_entity": from_id,
            "to_entity": to_id,
            "path": [],
            "length": 0,
            "found": False,
        }

    from_info = _extract_node_info(from_node)
    to_info = _extract_node_info(to_node)

    # Find shortest path (intermediate nodes in variable-length path are not
    # individually bound; anchor nodes were already scoped in entity lookup)
    query = f"""
    MATCH (a), (b)
    WHERE id(a) = $from_neo_id AND id(b) = $to_neo_id
    MATCH p = shortestPath((a)-[*..{max_depth}]-(b))
    RETURN nodes(p) AS nodes, relationships(p) AS rels
    """

    results = kg.execute_cypher(query, {
        "from_neo_id": from_node.id if hasattr(from_node, "id") else from_node.element_id,
        "to_neo_id": to_node.id if hasattr(to_node, "id") else to_node.element_id,
    })

    if not results:
        return {
            "from_entity": from_info,
            "to_entity": to_info,
            "path": [],
            "length": 0,
            "found": False,
        }

    row = results[0]
    path_nodes = row.get("nodes", [])
    path_rels = row.get("rels", [])

    path = []
    for i, node in enumerate(path_nodes):
        entry = {"node": _extract_node_info(node)}
        if i < len(path_rels):
            entry["relationship_type"] = path_rels[i].type if hasattr(path_rels[i], "type") else str(path_rels[i])
        path.append(entry)

    return {
        "from_entity": from_info,
        "to_entity": to_info,
        "path": path,
        "length": len(path_rels),
        "found": True,
    }


@router.get("/graph/connections")
async def find_all_paths(
    from_id: str = Query(..., description="ID or name of the starting entity"),
    to_id: str = Query(..., description="ID or name of the target entity"),
    max_depth: int = Query(3, ge=1, le=5, description="Maximum traversal depth (1-5)"),
    limit: int = Query(5, ge=1, le=20, description="Maximum number of paths to return"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Find all shortest paths between two entities in the knowledge graph.

    Returns multiple paths connecting the two entities, useful for discovering
    different biological routes or mechanisms linking them.

    **Examples:**
    - Find connections between a taxon and a disease:
      `/graph/connections?from_id=Akkermansia muciniphila&to_id=obesity`
    - Limit to 3 paths: `/graph/connections?from_id=butyrate&to_id=Parkinson's disease&limit=3`
    """
    kg = get_kg()

    # Look up both entities
    from_results = kg.execute_cypher(
        _find_entity_query().replace("$id", "$from_id"),
        {"from_id": from_id, **scope_params(scope)},
    )
    to_results = kg.execute_cypher(
        _find_entity_query().replace("$id", "$to_id"),
        {"to_id": to_id, **scope_params(scope)},
    )

    from_node = from_results[0].get("n") if from_results else None
    to_node = to_results[0].get("n") if to_results else None

    if from_node is None or to_node is None:
        return {
            "from_entity": from_id,
            "to_entity": to_id,
            "paths": [],
            "count": 0,
        }

    from_info = _extract_node_info(from_node)
    to_info = _extract_node_info(to_node)

    query = f"""
    MATCH (a), (b)
    WHERE id(a) = $from_neo_id AND id(b) = $to_neo_id
    MATCH p = allShortestPaths((a)-[*..{max_depth}]-(b))
    RETURN nodes(p) AS nodes, relationships(p) AS rels
    LIMIT $limit
    """

    results = kg.execute_cypher(query, {
        "from_neo_id": from_node.id if hasattr(from_node, "id") else from_node.element_id,
        "to_neo_id": to_node.id if hasattr(to_node, "id") else to_node.element_id,
        "limit": limit,
    })

    paths = []
    for row in results:
        path_nodes = row.get("nodes", [])
        path_rels = row.get("rels", [])
        path = []
        for i, node in enumerate(path_nodes):
            entry = {"node": _extract_node_info(node)}
            if i < len(path_rels):
                entry["relationship_type"] = path_rels[i].type if hasattr(path_rels[i], "type") else str(path_rels[i])
            path.append(entry)
        paths.append(path)

    return {
        "from_entity": from_info,
        "to_entity": to_info,
        "paths": paths,
        "count": len(paths),
    }


@router.get("/graph/neighborhood")
async def get_neighborhood(
    entity_id: str = Query(..., description="ID or name of the entity to explore"),
    max_depth: int = Query(2, ge=1, le=3, description="Maximum hop distance (1-3)"),
    limit: int = Query(50, ge=1, le=200, description="Maximum number of neighbors to return"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Get all entities within N hops of a given entity.

    Explores the local neighborhood around an entity, returning distinct neighbors
    with their distance and the relationship used to reach them.

    **Examples:**
    - Get neighbors of a taxon: `/graph/neighborhood?entity_id=Akkermansia muciniphila`
    - Explore 1-hop only: `/graph/neighborhood?entity_id=butyrate&max_depth=1`
    - Get more results: `/graph/neighborhood?entity_id=obesity&max_depth=3&limit=100`

    Useful for:
    - Exploring what entities are connected to a given node
    - Building local subgraph visualizations
    - Discovering indirect associations
    """
    kg = get_kg()

    # Look up the entity
    entity_results = kg.execute_cypher(
        _find_entity_query().replace("$id", "$entity_id"),
        {"entity_id": entity_id, **scope_params(scope)},
    )

    entity_node = entity_results[0].get("n") if entity_results else None

    if entity_node is None:
        return {
            "entity": entity_id,
            "neighbors": [],
            "count": 0,
        }

    entity_info = _extract_node_info(entity_node)

    query = _neighborhood_query(max_depth)

    results = kg.execute_cypher(query, {
        "neo_id": entity_node.id if hasattr(entity_node, "id") else entity_node.element_id,
        "limit": limit,
        **scope_params(scope),
    })

    neighbors = []
    for row in results:
        neighbor_node = row.get("neighbor")
        if neighbor_node is None:
            continue
        info = _extract_node_info(neighbor_node)
        info["distance"] = row.get("distance", 0)
        info["relationship_path"] = row.get("relationship_path", "")
        neighbors.append(info)

    return {
        "entity": entity_info,
        "neighbors": neighbors,
        "count": len(neighbors),
    }
