"""GPV-434 / #251: derive the metabolite->pathway->disease spine + genes.

Two idempotent derivations over the already-loaded graph:

  1. (:Pathway)-[:IMPLICATED_IN {support, source, method, hub_pathway_max,
     min_support, derived_at}]->(:Disease) for every (pathway, disease) pair
     sharing >= `min_support` distinct **non-hub** compounds, where each shared
     compound PARTICIPATES_IN the pathway and is LINKED_TO_DISEASE the disease.

     `support` = the number of distinct non-hub shared compounds.

     **#274 — noise control.** The bare compound-cooccurrence produced a spine
     that was 77-89% single-compound-support and dominated by promiscuous
     cofactors: NADP (234 pathways) alone fanned one legitimate link into 234
     "implicated" edges, so "Celecoxib Action Pathway implicated in Pellagra"
     surfaced as a finding. Two filters, both recorded on the edge for
     reproducibility:
       - **Hub exclusion** by a self-maintaining degree cutoff: a compound in
         more than `hub_pathway_max` pathways (default 40) is a cofactor, not a
         mechanistic marker, and is dropped from the co-occurrence. A degree
         cutoff maintains itself; a cofactor denylist rots (cf. #269/#271/#272).
       - **Support floor** `min_support` (default 2): a single shared compound
         is too thin to assert a pathway-disease mechanism.

     Because the old derivation was MERGE-only additive, re-running with the
     filters would NOT remove the already-materialised noise edges — so a
     cleanup pass DELETEs every existing IMPLICATED_IN whose recomputed non-hub
     support has dropped below `min_support` (#274).

  2. (:Gene {name, symbol})  +  (:Protein)-[:ENCODED_BY]->(:Gene)
     from Protein.gene_name. :Gene is keyed on `name` so reactome_loader's
     dormant `OPTIONAL MATCH (g:Gene {name})` linking can resolve.

Re-runnable: MERGE+SET+the bounded cleanup converge to the same graph for a
given (hub_pathway_max, min_support). Run AFTER the data load + dedup are
complete; exposed via `load_knowledge_graph.py --derive` (standalone).
"""
from __future__ import annotations

#: A compound in more than this many pathways is treated as a promiscuous
#: cofactor and excluded from the spine derivation (#274). Self-maintaining.
DEFAULT_HUB_PATHWAY_MAX = 40

#: Minimum distinct non-hub shared compounds for a pathway-disease edge (#274).
DEFAULT_MIN_SUPPORT = 2

#: Compounds in <= $hub_max pathways only; a pathway-disease pair needs
#: >= $min_support of them. Both bound as parameters and stamped on the edge.
PATHWAY_DISEASE_CYPHER = (
    "MATCH (c:Compound)-[:PARTICIPATES_IN]->(p:Pathway) "
    "WHERE COUNT { (c)-[:PARTICIPATES_IN]->(:Pathway) } <= $hub_max "
    "MATCH (c)-[:LINKED_TO_DISEASE]->(d:Disease) "
    "WITH p, d, count(DISTINCT c) AS support "
    "WHERE support >= $min_support "
    "MERGE (p)-[r:IMPLICATED_IN]->(d) "
    "SET r.support = support, r.source = 'derived', "
    "    r.method = 'compound-cooccurrence-nonhub', "
    "    r.hub_pathway_max = $hub_max, r.min_support = $min_support, "
    "    r.derived_at = datetime()"
)

#: Cleanup (#274): the derivation MERGEs qualifying edges but cannot remove
#: previously-materialised ones that no longer qualify — so delete every
#: existing IMPLICATED_IN whose recomputed non-hub support is below the floor.
CLEANUP_IMPLICATED_IN_CYPHER = (
    "MATCH (p:Pathway)-[r:IMPLICATED_IN]->(d:Disease) "
    "OPTIONAL MATCH (c:Compound)-[:PARTICIPATES_IN]->(p) "
    "  WHERE (c)-[:LINKED_TO_DISEASE]->(d) "
    "  AND COUNT { (c)-[:PARTICIPATES_IN]->(:Pathway) } <= $hub_max "
    "WITH r, count(DISTINCT c) AS nonhub_support "
    "WHERE nonhub_support < $min_support "
    "DELETE r"
)

GENES_CYPHER = (
    "MATCH (pr:Protein) "
    "WHERE pr.gene_name IS NOT NULL AND pr.gene_name <> '' "
    "MERGE (g:Gene {name: pr.gene_name}) "
    "  ON CREATE SET g.symbol = pr.gene_name, g.source = 'derived-from-protein', "
    "                g.organization_id = coalesce(pr.organization_id, 'default'), "
    "                g.created_at = datetime() "
    "MERGE (pr)-[e:ENCODED_BY]->(g) "
    "  ON CREATE SET e.source = 'derived-from-protein', e.derived_at = datetime()"
)


def derive(
    driver,
    database: str = "neo4j",
    hub_pathway_max: int = DEFAULT_HUB_PATHWAY_MAX,
    min_support: int = DEFAULT_MIN_SUPPORT,
) -> dict:
    """Run both derivations (idempotent) and return post-run totals.

    `hub_pathway_max` / `min_support` tune the spine noise filters (#274); the
    defaults (40 / 2) are the values decided for kgdev. Both are recorded on
    each derived edge so the spine stays reproducible and auditable.

    The pathway-disease pass MERGEs qualifying edges, then a cleanup DELETEs any
    pre-existing edge that no longer clears the filters — required because the
    MERGE alone cannot remove already-materialised noise.

    Returns {"implicated_in": int, "genes": int, "encoded_by": int} — totals in
    the graph after the run (not per-run deltas), so a canary reads the same
    numbers on a first or repeat run.
    """
    params = {"hub_max": hub_pathway_max, "min_support": min_support}
    with driver.session(database=database) as session:
        session.execute_write(lambda tx: tx.run(PATHWAY_DISEASE_CYPHER, **params).consume())
        session.execute_write(lambda tx: tx.run(CLEANUP_IMPLICATED_IN_CYPHER, **params).consume())
        session.execute_write(lambda tx: tx.run(GENES_CYPHER).consume())
        counts = {
            "implicated_in": session.execute_read(lambda tx: tx.run(
                "MATCH ()-[r:IMPLICATED_IN]->() RETURN count(r) AS n").single()["n"]),
            "genes": session.execute_read(lambda tx: tx.run(
                "MATCH (g:Gene) RETURN count(g) AS n").single()["n"]),
            "encoded_by": session.execute_read(lambda tx: tx.run(
                "MATCH ()-[r:ENCODED_BY]->() RETURN count(r) AS n").single()["n"]),
        }
    return counts
