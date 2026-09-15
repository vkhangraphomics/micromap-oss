"""
Entity deduplication and normalization for the MicroMap knowledge graph.

Finds and merges duplicate nodes (diseases, taxa, metabolites) that
represent the same real-world entity but were ingested from different
data sources with slightly different names or identifiers.
"""

import logging
from typing import List, Dict, Any

from neo4j import Driver

from database.ingestion.base_loader import normalize_disease_name

logger = logging.getLogger(__name__)


def find_duplicate_diseases(driver: Driver, database: str) -> List[Dict[str, Any]]:
    """
    Find Disease nodes with similar normalized names that are likely duplicates.

    Compares name_normalized values and also re-normalizes raw names using
    the enhanced normalize_disease_name() to catch variants that were
    ingested before normalization improvements.

    Args:
        driver: Neo4j driver instance
        database: Neo4j database name

    Returns:
        List of dicts with 'normalized_name', 'node_ids', 'names', 'count'
    """
    duplicates = []

    with driver.session(database=database) as session:
        # Find diseases that share the same normalized name
        result = session.run("""
            MATCH (d:Disease)
            WITH d,
                 CASE WHEN d.name_normalized IS NOT NULL
                      THEN d.name_normalized
                      ELSE toLower(trim(d.name))
                 END AS norm_name
            WITH norm_name, collect(d) AS nodes
            WHERE size(nodes) > 1
            RETURN norm_name,
                   [n IN nodes | elementId(n)] AS node_ids,
                   [n IN nodes | n.name] AS names,
                   size(nodes) AS count
            ORDER BY count DESC
        """)

        for record in result:
            duplicates.append({
                "normalized_name": record["norm_name"],
                "node_ids": record["node_ids"],
                "names": record["names"],
                "count": record["count"],
            })

    # Second pass: re-normalize with enhanced function to catch
    # abbreviation variants (e.g., "IBD" vs "inflammatory bowel disease")
    with driver.session(database=database) as session:
        result = session.run("""
            MATCH (d:Disease)
            RETURN elementId(d) AS node_id, d.name AS name,
                   d.name_normalized AS name_normalized
        """)

        renorm_groups: Dict[str, List[Dict[str, str]]] = {}
        for record in result:
            raw_name = record["name"] or ""
            enhanced_norm = normalize_disease_name(raw_name)
            if not enhanced_norm:
                continue
            renorm_groups.setdefault(enhanced_norm, []).append({
                "node_id": record["node_id"],
                "name": raw_name,
                "name_normalized": record["name_normalized"],
            })

        # Only include groups that have duplicates and were not already found
        existing_norm_names = {d["normalized_name"] for d in duplicates}
        for norm_name, nodes in renorm_groups.items():
            if len(nodes) > 1 and norm_name not in existing_norm_names:
                duplicates.append({
                    "normalized_name": norm_name,
                    "node_ids": [n["node_id"] for n in nodes],
                    "names": [n["name"] for n in nodes],
                    "count": len(nodes),
                })

    if duplicates:
        logger.info(f"Found {len(duplicates)} groups of duplicate diseases")
    else:
        logger.info("No duplicate diseases found")

    return duplicates


def find_duplicate_taxa(driver: Driver, database: str) -> List[Dict[str, Any]]:
    """
    Find Taxon nodes with the same NCBI taxonomy ID but different node IDs.

    Args:
        driver: Neo4j driver instance
        database: Neo4j database name

    Returns:
        List of dicts with 'ncbi_tax_id', 'node_ids', 'names', 'count'
    """
    duplicates = []

    with driver.session(database=database) as session:
        result = session.run("""
            MATCH (t:Taxon)
            WHERE t.ncbi_tax_id IS NOT NULL
            WITH t.ncbi_tax_id AS ncbi_id, collect(t) AS nodes
            WHERE size(nodes) > 1
            RETURN ncbi_id,
                   [n IN nodes | elementId(n)] AS node_ids,
                   [n IN nodes | n.name] AS names,
                   size(nodes) AS count
            ORDER BY count DESC
        """)

        for record in result:
            duplicates.append({
                "ncbi_tax_id": record["ncbi_id"],
                "node_ids": record["node_ids"],
                "names": record["names"],
                "count": record["count"],
            })

    if duplicates:
        logger.info(f"Found {len(duplicates)} groups of duplicate taxa")
    else:
        logger.info("No duplicate taxa found")

    return duplicates


def find_duplicate_metabolites(driver: Driver, database: str) -> List[Dict[str, Any]]:
    """
    Find Metabolite nodes with the same HMDB or KEGG identifiers.

    Args:
        driver: Neo4j driver instance
        database: Neo4j database name

    Returns:
        List of dicts with 'identifier', 'id_type', 'node_ids', 'names', 'count'
    """
    duplicates = []

    with driver.session(database=database) as session:
        # Check HMDB ID duplicates
        result = session.run("""
            MATCH (m:Compound)
            WHERE m.hmdb_id IS NOT NULL AND m.hmdb_id <> ''
            WITH m.hmdb_id AS hmdb_id, collect(m) AS nodes
            WHERE size(nodes) > 1
            RETURN hmdb_id AS identifier,
                   [n IN nodes | elementId(n)] AS node_ids,
                   [n IN nodes | n.name] AS names,
                   size(nodes) AS count
            ORDER BY count DESC
        """)

        for record in result:
            duplicates.append({
                "identifier": record["identifier"],
                "id_type": "hmdb_id",
                "node_ids": record["node_ids"],
                "names": record["names"],
                "count": record["count"],
            })

        # Check KEGG ID duplicates
        result = session.run("""
            MATCH (m:Compound)
            WHERE m.kegg_id IS NOT NULL AND m.kegg_id <> ''
            WITH m.kegg_id AS kegg_id, collect(m) AS nodes
            WHERE size(nodes) > 1
            RETURN kegg_id AS identifier,
                   [n IN nodes | elementId(n)] AS node_ids,
                   [n IN nodes | n.name] AS names,
                   size(nodes) AS count
            ORDER BY count DESC
        """)

        for record in result:
            duplicates.append({
                "identifier": record["identifier"],
                "id_type": "kegg_id",
                "node_ids": record["node_ids"],
                "names": record["names"],
                "count": record["count"],
            })

    if duplicates:
        logger.info(f"Found {len(duplicates)} groups of duplicate metabolites")
    else:
        logger.info("No duplicate metabolites found")

    return duplicates


def merge_duplicate_nodes(
    driver: Driver,
    database: str,
    label: str,
    keep_id: str,
    merge_ids: List[str],
) -> Dict[str, int]:
    """
    Merge duplicate nodes by transferring all relationships from merge_ids
    nodes to the keep_id node, then deleting the merge_ids nodes.

    Properties from merged nodes are preserved on the kept node when they
    are missing (first-write-wins for scalar properties; lists are merged).

    Args:
        driver: Neo4j driver instance
        database: Neo4j database name
        label: Node label (e.g., 'Disease', 'Taxon', 'Metabolite')
        keep_id: Element ID of the node to keep
        merge_ids: Element IDs of nodes to merge into the kept node

    Returns:
        Dict with 'relationships_transferred' and 'nodes_deleted' counts
    """
    stats = {"relationships_transferred": 0, "nodes_deleted": 0}

    if not merge_ids:
        return stats

    with driver.session(database=database) as session:
        for merge_id in merge_ids:
            # Transfer incoming relationships
            result = session.run("""
                MATCH (keep) WHERE elementId(keep) = $keep_id
                MATCH (dup) WHERE elementId(dup) = $merge_id
                MATCH (source)-[r]->(dup)
                WHERE source <> keep
                WITH keep, dup, source, type(r) AS rel_type, properties(r) AS rel_props, r
                DELETE r
                WITH keep, source, rel_type, rel_props
                CALL apoc.create.relationship(source, rel_type, rel_props, keep)
                YIELD rel
                RETURN count(rel) AS transferred
            """, keep_id=keep_id, merge_id=merge_id)

            record = result.single()
            if record:
                stats["relationships_transferred"] += record["transferred"]

            # Transfer outgoing relationships
            result = session.run("""
                MATCH (keep) WHERE elementId(keep) = $keep_id
                MATCH (dup) WHERE elementId(dup) = $merge_id
                MATCH (dup)-[r]->(target)
                WHERE target <> keep
                WITH keep, dup, target, type(r) AS rel_type, properties(r) AS rel_props, r
                DELETE r
                WITH keep, target, rel_type, rel_props
                CALL apoc.create.relationship(keep, rel_type, rel_props, target)
                YIELD rel
                RETURN count(rel) AS transferred
            """, keep_id=keep_id, merge_id=merge_id)

            record = result.single()
            if record:
                stats["relationships_transferred"] += record["transferred"]

            # Copy non-null properties from duplicate to kept node
            # (only fills in missing properties, does not overwrite)
            session.run("""
                MATCH (keep) WHERE elementId(keep) = $keep_id
                MATCH (dup) WHERE elementId(dup) = $merge_id
                WITH keep, dup, keys(dup) AS props
                UNWIND props AS prop
                WITH keep, dup, prop
                WHERE keep[prop] IS NULL AND prop <> 'name'
                CALL apoc.create.setProperty(keep, prop, dup[prop])
                YIELD node
                RETURN count(node)
            """, keep_id=keep_id, merge_id=merge_id)

            # Delete remaining relationships and the duplicate node
            session.run("""
                MATCH (dup) WHERE elementId(dup) = $merge_id
                DETACH DELETE dup
            """, merge_id=merge_id)

            stats["nodes_deleted"] += 1

    logger.info(
        f"Merged {stats['nodes_deleted']} duplicate {label} nodes, "
        f"transferred {stats['relationships_transferred']} relationships"
    )

    return stats


def run_deduplication(driver: Driver, database: str) -> Dict[str, Any]:
    """
    Run a full deduplication pass across all entity types.

    Finds duplicates for diseases, taxa, and metabolites, then merges
    them by transferring relationships to a canonical node.

    Args:
        driver: Neo4j driver instance
        database: Neo4j database name

    Returns:
        Summary report dict with counts per entity type
    """
    report = {
        "diseases": {"duplicates_found": 0, "nodes_merged": 0, "relationships_transferred": 0},
        "taxa": {"duplicates_found": 0, "nodes_merged": 0, "relationships_transferred": 0},
        "metabolites": {"duplicates_found": 0, "nodes_merged": 0, "relationships_transferred": 0},
    }

    # --- Diseases ---
    logger.info("Scanning for duplicate diseases...")
    disease_dupes = find_duplicate_diseases(driver, database)
    report["diseases"]["duplicates_found"] = len(disease_dupes)

    for group in disease_dupes:
        keep_id = group["node_ids"][0]
        merge_ids = group["node_ids"][1:]
        stats = merge_duplicate_nodes(driver, database, "Disease", keep_id, merge_ids)
        report["diseases"]["nodes_merged"] += stats["nodes_deleted"]
        report["diseases"]["relationships_transferred"] += stats["relationships_transferred"]

    # --- Taxa ---
    logger.info("Scanning for duplicate taxa...")
    taxa_dupes = find_duplicate_taxa(driver, database)
    report["taxa"]["duplicates_found"] = len(taxa_dupes)

    for group in taxa_dupes:
        keep_id = group["node_ids"][0]
        merge_ids = group["node_ids"][1:]
        stats = merge_duplicate_nodes(driver, database, "Taxon", keep_id, merge_ids)
        report["taxa"]["nodes_merged"] += stats["nodes_deleted"]
        report["taxa"]["relationships_transferred"] += stats["relationships_transferred"]

    # --- Metabolites ---
    logger.info("Scanning for duplicate metabolites...")
    metabolite_dupes = find_duplicate_metabolites(driver, database)
    report["metabolites"]["duplicates_found"] = len(metabolite_dupes)

    for group in metabolite_dupes:
        keep_id = group["node_ids"][0]
        merge_ids = group["node_ids"][1:]
        stats = merge_duplicate_nodes(driver, database, "Metabolite", keep_id, merge_ids)
        report["metabolites"]["nodes_merged"] += stats["nodes_deleted"]
        report["metabolites"]["relationships_transferred"] += stats["relationships_transferred"]

    # Print summary
    print("\n" + "=" * 60)
    print("DEDUPLICATION REPORT")
    print("=" * 60)
    for entity_type, counts in report.items():
        print(f"\n  {entity_type.upper()}:")
        print(f"    Duplicate groups found: {counts['duplicates_found']}")
        print(f"    Nodes merged:           {counts['nodes_merged']}")
        print(f"    Relationships moved:    {counts['relationships_transferred']}")
    print("\n" + "=" * 60 + "\n")

    return report
