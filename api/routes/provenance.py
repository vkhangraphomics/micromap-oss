"""
Provenance API routes.

Provides data provenance tracking endpoints for understanding
data sources, coverage, and lineage across the knowledge graph.
"""

from fastapi import APIRouter, HTTPException
from typing import Optional

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


@router.get("/provenance/sources")
async def list_data_sources():
    """
    List all data sources with entity counts.

    Aggregates across all node types, unwinding the `sources` property
    to count how many nodes of each label came from each source.

    **Example response:**
    ```json
    {
        "sources": [
            {
                "name": "disbiome",
                "node_counts": {"Taxon": 500, "Disease": 120},
                "total_nodes": 620,
                "relationship_count": 4200
            }
        ]
    }
    ```
    """
    kg = get_kg()

    labels = ["Taxon", "Disease", "Metabolite", "Drug", "Gene", "Pathway", "Paper"]

    # Aggregate source counts per label
    source_data = {}

    for label in labels:
        # Issue #83: some loaders set only the scalar `n.source` (ChEMBL,
        # PubMed/Paper, …), others set the list `n.sources` (disbiome,
        # bugsigdb, …). Union both — COALESCE falls back to a singleton list
        # built from the scalar so neither shape is silently invisible.
        query = f"""
        MATCH (n:{label})
        WITH n, COALESCE(
            n.sources,
            CASE WHEN n.source IS NOT NULL THEN [n.source] ELSE NULL END
        ) AS srcs
        WHERE srcs IS NOT NULL AND size(srcs) > 0
        UNWIND srcs AS source
        RETURN source AS name, count(n) AS cnt
        """
        results = kg.execute_cypher(query, {})
        for row in results:
            src_name = row["name"]
            if src_name not in source_data:
                source_data[src_name] = {"node_counts": {}, "total_nodes": 0, "relationship_count": 0}
            source_data[src_name]["node_counts"][label] = row["cnt"]
            source_data[src_name]["total_nodes"] += row["cnt"]

    # Count relationships per source
    rel_query = """
    MATCH ()-[r]->()
    WHERE r.source IS NOT NULL
    RETURN r.source AS name, count(r) AS cnt
    """
    rel_results = kg.execute_cypher(rel_query, {})
    for row in rel_results:
        src_name = row["name"]
        if src_name not in source_data:
            source_data[src_name] = {"node_counts": {}, "total_nodes": 0, "relationship_count": 0}
        source_data[src_name]["relationship_count"] = row["cnt"]

    sources = [
        {
            "name": name,
            "node_counts": data["node_counts"],
            "total_nodes": data["total_nodes"],
            "relationship_count": data["relationship_count"],
        }
        for name, data in sorted(source_data.items())
    ]

    return {"sources": sources}


@router.get("/provenance/entity/{entity_id}")
async def get_entity_provenance(entity_id: str):
    """
    Get provenance information for a specific entity.

    Looks up the entity across all label types (Taxon, Disease, Metabolite,
    Drug, Gene, Pathway, Paper) and returns its source lineage, properties,
    and relationship summary.

    **Example:** `/provenance/entity/239935` returns provenance for taxon 239935.
    """
    kg = get_kg()

    # Search across all label types using various ID properties
    labels_and_id_fields = [
        ("Taxon", "taxon_id"),
        ("Disease", "name_normalized"),
        ("Metabolite", "id"),
        ("Drug", "id"),
        ("Gene", "id"),
        ("Pathway", "id"),
        ("Paper", "id"),
    ]

    for label, id_field in labels_and_id_fields:
        query = f"""
        MATCH (n:{label})
        WHERE n.{id_field} = $entity_id OR n.name = $entity_id
        RETURN n
        LIMIT 1
        """
        results = kg.execute_cypher(query, {"entity_id": entity_id})
        if results:
            node = results[0]["n"]
            entity_type = label

            # Get relationship counts by type
            rel_query = f"""
            MATCH (n:{label})
            WHERE n.{id_field} = $entity_id OR n.name = $entity_id
            MATCH (n)-[r]-()
            RETURN type(r) AS rel_type, count(r) AS cnt
            """
            rel_results = kg.execute_cypher(rel_query, {"entity_id": entity_id})
            relationships = {row["rel_type"]: row["cnt"] for row in rel_results}

            properties = dict(node)
            sources = properties.pop("sources", []) if "sources" in properties else []
            created_at = properties.pop("created_at", None) if "created_at" in properties else None

            return {
                "entity_id": entity_id,
                "entity_type": entity_type,
                "name": properties.get("name", entity_id),
                "sources": sources,
                "created_at": created_at,
                "properties": properties,
                "relationships": relationships,
            }

    raise HTTPException(status_code=404, detail=f"Entity '{entity_id}' not found")


@router.get("/provenance/stats")
async def get_provenance_stats():
    """
    Get overall provenance statistics.

    Returns coverage metrics showing how many nodes and relationships
    have source provenance attached, broken down by label.

    **Example response:**
    ```json
    {
        "total_nodes": 1100000,
        "nodes_with_sources": 950000,
        "nodes_without_sources": 150000,
        "coverage_pct": 86.4,
        "by_label": {
            "Taxon": {"total": 1050000, "with_sources": 900000, "without_sources": 150000}
        }
    }
    ```
    """
    kg = get_kg()

    labels = ["Taxon", "Disease", "Metabolite", "Drug", "Gene", "Pathway", "Paper"]

    total_nodes = 0
    nodes_with_sources = 0
    nodes_without_sources = 0
    by_label = {}

    for label in labels:
        query = f"""
        MATCH (n:{label})
        RETURN
            count(n) AS total,
            count(CASE WHEN n.sources IS NOT NULL THEN 1 END) AS with_sources,
            count(CASE WHEN n.sources IS NULL THEN 1 END) AS without_sources
        """
        results = kg.execute_cypher(query, {})
        if results:
            row = results[0]
            if row["total"] > 0:
                by_label[label] = {
                    "total": row["total"],
                    "with_sources": row["with_sources"],
                    "without_sources": row["without_sources"],
                }
                total_nodes += row["total"]
                nodes_with_sources += row["with_sources"]
                nodes_without_sources += row["without_sources"]

    coverage_pct = round((nodes_with_sources / total_nodes * 100), 1) if total_nodes > 0 else 0.0

    return {
        "total_nodes": total_nodes,
        "nodes_with_sources": nodes_with_sources,
        "nodes_without_sources": nodes_without_sources,
        "coverage_pct": coverage_pct,
        "by_label": by_label,
    }
