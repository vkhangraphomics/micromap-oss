"""
Backfill provenance (source) properties on all nodes and relationships.

Uses batched transactions to avoid Neo4j memory limits.
Each batch processes a limited number of records using SKIP/LIMIT.
"""

import os
import time
from neo4j import GraphDatabase

BATCH_SIZE = 5000

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "password")
NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE", "graphomics")


def batch_update_nodes(driver, label, condition, set_clause, source_name, batch_size=BATCH_SIZE):
    """Update nodes in batches using SKIP/LIMIT to avoid OOM."""
    # First count how many need updating
    with driver.session(database=NEO4J_DATABASE) as session:
        result = session.run(f"MATCH (n:{label}) WHERE {condition} RETURN count(n) AS cnt")
        total = result.single()["cnt"]

    if total == 0:
        print(f"  {label}: 0 nodes need updating, skipping")
        return 0

    print(f"  {label}: {total:,} nodes to update with source='{source_name}'")
    updated = 0
    batch_num = 0

    while updated < total:
        with driver.session(database=NEO4J_DATABASE) as session:
            query = f"""
                MATCH (n:{label})
                WHERE {condition}
                WITH n LIMIT {batch_size}
                SET {set_clause}
                RETURN count(n) AS cnt
            """
            result = session.run(query)
            count = result.single()["cnt"]

        if count == 0:
            break

        updated += count
        batch_num += 1
        if batch_num % 10 == 0 or updated >= total:
            print(f"    {updated:,}/{total:,} ({100*updated//total}%)")

    print(f"  {label}: done ({updated:,} updated)")
    return updated


def batch_update_rels(driver, rel_type, condition, set_clause, source_name, batch_size=BATCH_SIZE):
    """Update relationships in batches."""
    with driver.session(database=NEO4J_DATABASE) as session:
        result = session.run(
            f"MATCH ()-[r:{rel_type}]->() WHERE {condition} RETURN count(r) AS cnt"
        )
        total = result.single()["cnt"]

    if total == 0:
        print(f"  {rel_type}: 0 rels need updating, skipping")
        return 0

    print(f"  {rel_type}: {total:,} rels to update with source='{source_name}'")
    updated = 0
    batch_num = 0

    while updated < total:
        with driver.session(database=NEO4J_DATABASE) as session:
            query = f"""
                MATCH ()-[r:{rel_type}]->()
                WHERE {condition}
                WITH r LIMIT {batch_size}
                SET {set_clause}
                RETURN count(r) AS cnt
            """
            result = session.run(query)
            count = result.single()["cnt"]

        if count == 0:
            break

        updated += count
        batch_num += 1
        if batch_num % 10 == 0 or updated >= total:
            print(f"    {updated:,}/{total:,} ({100*updated//total}%)")

    print(f"  {rel_type}: done ({updated:,} updated)")
    return updated


def main():
    print(f"Connecting to Neo4j at {NEO4J_URI}...")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver.verify_connectivity()
    print("Connected.\n")

    start = time.time()
    total_updated = 0

    # === NODE PROVENANCE ===
    print("=" * 60)
    print("BACKFILLING NODE PROVENANCE")
    print("=" * 60)

    # Issue #83: sync the `sources` list from the scalar `n.source` for any
    # node where a loader set only the scalar form (ChEMBL drug, ChEMBL
    # protein, PubMed paper, pubchem compound, …). The existing
    # default-missing passes below have the guard
    # `n.source IS NULL AND (n.sources IS NULL OR size(n.sources) = 0)`
    # which skipped these nodes because they already had a scalar source —
    # they were never repaired and the /provenance/sources endpoint (which
    # only counted `n.sources`) silently missed ~19k production nodes.
    print("\nSyncing sources list from scalar source (issue #83 repair)...")
    for label in (
        "Taxon", "Disease", "Metabolite", "Protein", "Pathway", "Drug",
        "Gene", "Paper", "Study", "Subject", "Sample", "BodySite",
    ):
        total_updated += batch_update_nodes(
            driver, label,
            "n.source IS NOT NULL AND (n.sources IS NULL OR size(n.sources) = 0)",
            "n.sources = [n.source]",
            "[from scalar source]",
        )

    # Taxon nodes without source -> ncbi_taxonomy
    total_updated += batch_update_nodes(
        driver, "Taxon",
        "n.source IS NULL AND (n.sources IS NULL OR size(n.sources) = 0)",
        "n.source = 'ncbi_taxonomy', n.sources = ['ncbi_taxonomy']",
        "ncbi_taxonomy",
    )

    # Taxon nodes that have sources array but no source scalar
    total_updated += batch_update_nodes(
        driver, "Taxon",
        "n.source IS NULL AND n.sources IS NOT NULL AND size(n.sources) > 0",
        "n.source = n.sources[0]",
        "from sources array",
    )

    # Disease nodes - set source from sources array if missing
    total_updated += batch_update_nodes(
        driver, "Disease",
        "n.source IS NULL AND n.sources IS NOT NULL AND size(n.sources) > 0",
        "n.source = n.sources[0]",
        "from sources array",
    )

    # Disease nodes with no source at all
    total_updated += batch_update_nodes(
        driver, "Disease",
        "n.source IS NULL AND (n.sources IS NULL OR size(n.sources) = 0)",
        "n.source = 'curated', n.sources = ['curated']",
        "curated",
    )

    # Metabolite nodes without source -> kegg (KEGG loader is the main source)
    total_updated += batch_update_nodes(
        driver, "Metabolite",
        "n.source IS NULL AND (n.sources IS NULL OR size(n.sources) = 0)",
        "n.source = 'kegg', n.sources = ['kegg']",
        "kegg",
    )

    # Protein nodes without source -> reactome
    total_updated += batch_update_nodes(
        driver, "Protein",
        "n.source IS NULL AND (n.sources IS NULL OR size(n.sources) = 0)",
        "n.source = 'reactome', n.sources = ['reactome']",
        "reactome",
    )

    # Pathway nodes without source -> kegg
    total_updated += batch_update_nodes(
        driver, "Pathway",
        "n.source IS NULL AND (n.sources IS NULL OR size(n.sources) = 0)",
        "n.source = 'kegg', n.sources = ['kegg']",
        "kegg",
    )

    # Drug nodes without source -> drugbank
    total_updated += batch_update_nodes(
        driver, "Drug",
        "n.source IS NULL AND (n.sources IS NULL OR size(n.sources) = 0)",
        "n.source = 'drugbank', n.sources = ['drugbank']",
        "drugbank",
    )

    # Gene nodes without source -> ncbi
    total_updated += batch_update_nodes(
        driver, "Gene",
        "n.source IS NULL AND (n.sources IS NULL OR size(n.sources) = 0)",
        "n.source = 'ncbi', n.sources = ['ncbi']",
        "ncbi",
    )

    # Paper nodes without source -> PubMed
    total_updated += batch_update_nodes(
        driver, "Paper",
        "n.source IS NULL AND (n.sources IS NULL OR size(n.sources) = 0)",
        "n.source = 'PubMed', n.sources = ['PubMed']",
        "PubMed",
    )

    # Study nodes without source
    total_updated += batch_update_nodes(
        driver, "Study",
        "n.source IS NULL AND (n.sources IS NULL OR size(n.sources) = 0)",
        "n.source = 'BugSigDB', n.sources = ['BugSigDB']",
        "BugSigDB",
    )

    # === RELATIONSHIP PROVENANCE ===
    print("\n" + "=" * 60)
    print("BACKFILLING RELATIONSHIP PROVENANCE")
    print("=" * 60)

    # HAS_PARENT -> ncbi_taxonomy
    total_updated += batch_update_rels(
        driver, "HAS_PARENT",
        "r.source IS NULL",
        "r.source = 'ncbi_taxonomy'",
        "ncbi_taxonomy",
    )

    # CHILD_OF -> ncbi_taxonomy
    total_updated += batch_update_rels(
        driver, "CHILD_OF",
        "r.source IS NULL",
        "r.source = 'ncbi_taxonomy'",
        "ncbi_taxonomy",
    )

    # MENTIONED_IN -> PubMed
    total_updated += batch_update_rels(
        driver, "MENTIONED_IN",
        "r.source IS NULL",
        "r.source = 'PubMed'",
        "PubMed",
    )

    # ASSOCIATED_WITH_DISEASE - set source from sources array if missing
    total_updated += batch_update_rels(
        driver, "ASSOCIATED_WITH_DISEASE",
        "r.source IS NULL AND r.sources IS NOT NULL AND size(r.sources) > 0",
        "r.source = r.sources[0]",
        "from sources array",
    )

    # ASSOCIATED_WITH_DISEASE with no source at all
    total_updated += batch_update_rels(
        driver, "ASSOCIATED_WITH_DISEASE",
        "r.source IS NULL AND (r.sources IS NULL OR size(r.sources) = 0)",
        "r.source = 'curated'",
        "curated",
    )

    # LINKED_TO_DISEASE
    total_updated += batch_update_rels(
        driver, "LINKED_TO_DISEASE",
        "r.source IS NULL AND r.sources IS NOT NULL AND size(r.sources) > 0",
        "r.source = r.sources[0]",
        "from sources array",
    )

    total_updated += batch_update_rels(
        driver, "LINKED_TO_DISEASE",
        "r.source IS NULL AND (r.sources IS NULL OR size(r.sources) = 0)",
        "r.source = 'hmdb'",
        "hmdb",
    )

    # PRODUCES
    total_updated += batch_update_rels(
        driver, "PRODUCES",
        "r.source IS NULL",
        "r.source = 'curated'",
        "curated",
    )

    # PARTICIPATES_IN -> kegg
    total_updated += batch_update_rels(
        driver, "PARTICIPATES_IN",
        "r.source IS NULL",
        "r.source = 'kegg'",
        "kegg",
    )

    # TARGETS
    total_updated += batch_update_rels(
        driver, "TARGETS",
        "r.source IS NULL",
        "r.source = 'drugbank'",
        "drugbank",
    )

    # SAME_AS
    total_updated += batch_update_rels(
        driver, "SAME_AS",
        "r.source IS NULL",
        "r.source = 'cross_reference'",
        "cross_reference",
    )

    # PROCESSES
    total_updated += batch_update_rels(
        driver, "PROCESSES",
        "r.source IS NULL",
        "r.source = 'reactome'",
        "reactome",
    )

    elapsed = time.time() - start
    print(f"\n{'=' * 60}")
    print("PROVENANCE BACKFILL COMPLETE")
    print(f"Total updated: {total_updated:,}")
    print(f"Time: {elapsed:.1f}s")
    print(f"{'=' * 60}")

    # === VERIFICATION ===
    print("\nVERIFICATION: Checking for remaining gaps...")
    with driver.session(database=NEO4J_DATABASE) as session:
        for label in ["Taxon", "Disease", "Metabolite", "Pathway", "Gene", "Drug", "Protein", "Paper", "Study"]:
            result = session.run(
                f"MATCH (n:{label}) WHERE n.source IS NULL RETURN count(n) AS cnt"
            )
            cnt = result.single()["cnt"]
            status = "OK" if cnt == 0 else f"GAPS: {cnt:,}"
            print(f"  {label}: {status}")

        for rel_type in ["HAS_PARENT", "CHILD_OF", "ASSOCIATED_WITH_DISEASE", "LINKED_TO_DISEASE",
                         "PRODUCES", "PARTICIPATES_IN", "MENTIONED_IN", "TARGETS", "SAME_AS", "PROCESSES",
                         "IMPLICATED_IN", "ENCODED_BY"]:
            result = session.run(
                f"MATCH ()-[r:{rel_type}]->() WHERE r.source IS NULL RETURN count(r) AS cnt"
            )
            cnt = result.single()["cnt"]
            status = "OK" if cnt == 0 else f"GAPS: {cnt:,}"
            print(f"  {rel_type}: {status}")

    driver.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
