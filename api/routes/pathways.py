"""
Pathway-related API routes.

Provides endpoints for querying metabolic pathways, their metabolites,
genes, and associated taxa.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

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


@router.get("/pathways")
async def list_pathways(
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    search: Optional[str] = Query(None, description="Search by pathway name"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    List all pathways in the knowledge graph.

    Returns pathways with their metabolite counts (number of metabolites
    that participate in each pathway).

    **Examples:**
    - Search for TCA: `?search=TCA`
    - Paginate: `?limit=20&offset=40`
    """
    kg = get_kg()

    where_clauses = []
    if search:
        where_clauses.append("p.name =~ $search_pattern")
    # Org-scope filter is always present (#200)
    where_clauses.append(ORG_FILTER("p"))

    where_clause = "WHERE " + " AND ".join(where_clauses)

    count_query = f"""
    MATCH (p:Pathway)
    {where_clause}
    RETURN count(p) AS total
    """

    query = f"""
    MATCH (p:Pathway)
    {where_clause}
    OPTIONAL MATCH (m:Compound)-[:PARTICIPATES_IN]->(p)
    WHERE {ORG_FILTER("m")}
    WITH p, count(DISTINCT m) AS metabolite_count
    RETURN p.pathway_id AS pathway_id,
           p.kegg_id AS kegg_id,
           p.name AS name,
           p.description AS description,
           metabolite_count
    ORDER BY p.name
    SKIP $offset
    LIMIT $limit
    """

    params = {
        "search_pattern": f"(?i).*{search}.*" if search else None,
        "offset": offset,
        "limit": limit,
    }
    params.update(scope_params(scope))

    count_result = kg.execute_cypher(count_query, params)
    total = count_result[0]["total"] if count_result else 0

    results = kg.execute_cypher(query, params)

    pathways = [
        {
            "pathway_id": r.get("pathway_id"),
            "kegg_id": r.get("kegg_id"),
            "name": r.get("name"),
            "description": r.get("description"),
            "metabolite_count": r["metabolite_count"],
        }
        for r in results
    ]

    return {
        "pathways": pathways,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/pathways/search/{query}")
async def search_pathways(
    query: str,
    limit: int = Query(20, ge=1, le=100, description="Maximum number of results"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Search pathways by name with relevance scoring.

    Scores: exact match = 0 (best), prefix match = 1, partial match = 2.

    **Example:** `/pathways/search/glycolysis`
    """
    kg = get_kg()

    cypher = """
    MATCH (p:Pathway)
    WHERE p.name =~ $pattern
      AND (p.organization_id = $organization_id OR p.organization_id IN $public_orgs)
    WITH p,
         CASE
             WHEN toLower(p.name) = toLower($query) THEN 0
             WHEN toLower(p.name) STARTS WITH toLower($query) THEN 1
             ELSE 2
         END AS score
    RETURN p.pathway_id AS pathway_id,
           p.kegg_id AS kegg_id,
           p.name AS name,
           p.description AS description,
           score
    ORDER BY score, p.name
    LIMIT $limit
    """

    params = {
        "query": query,
        "pattern": f"(?i).*{query}.*",
        "limit": limit,
    }
    params.update(scope_params(scope))
    results = kg.execute_cypher(cypher, params)

    return {
        "query": query,
        "results": [
            {
                "pathway_id": r.get("pathway_id"),
                "kegg_id": r.get("kegg_id"),
                "name": r.get("name"),
                "description": r.get("description"),
                "score": r["score"],
            }
            for r in results
        ],
        "count": len(results),
    }


@router.get("/pathways/{pathway_id}")
async def get_pathway(
    pathway_id: str,
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Get detailed information about a specific pathway.

    **Accepted pathway_id formats:**
    - Pathway ID: e.g. `map00010`
    - KEGG ID: e.g. `hsa00010`
    - Name: e.g. `Glycolysis`

    Returns pathway details including counts of metabolites and genes.
    """
    kg = get_kg()

    query = """
    MATCH (p:Pathway)
    WHERE (p.pathway_id = $id
       OR p.kegg_id = $id
       OR toLower(p.name) = toLower($id))
      AND (p.organization_id = $organization_id OR p.organization_id IN $public_orgs)
    OPTIONAL MATCH (m:Compound)-[:PARTICIPATES_IN]->(p)
    WHERE (m.organization_id = $organization_id OR m.organization_id IN $public_orgs)
    WITH p, count(DISTINCT m) AS metabolite_count
    OPTIONAL MATCH (g:Gene)-[:PARTICIPATES_IN]->(p)
    WHERE (g.organization_id = $organization_id OR g.organization_id IN $public_orgs)
    WITH p, metabolite_count, count(DISTINCT g) AS gene_count
    RETURN p.pathway_id AS pathway_id,
           p.kegg_id AS kegg_id,
           p.name AS name,
           p.description AS description,
           metabolite_count,
           gene_count
    """

    params = {"id": pathway_id}
    params.update(scope_params(scope))
    results = kg.execute_cypher(query, params)

    if not results:
        raise HTTPException(status_code=404, detail=f"Pathway '{pathway_id}' not found")

    r = results[0]
    return {
        "pathway_id": r.get("pathway_id"),
        "kegg_id": r.get("kegg_id"),
        "name": r.get("name"),
        "description": r.get("description"),
        "metabolite_count": r["metabolite_count"],
        "gene_count": r["gene_count"],
    }


@router.get("/pathways/{pathway_id}/metabolites")
async def get_pathway_metabolites(
    pathway_id: str,
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Get all metabolites that participate in a specific pathway.

    Returns metabolites with `PARTICIPATES_IN` relationships to this pathway.

    **Example:** `/pathways/map00010/metabolites`
    """
    kg = get_kg()

    # Verify pathway exists
    pathway_check = kg.execute_cypher(
        """
        MATCH (p:Pathway)
        WHERE (p.pathway_id = $id
           OR p.kegg_id = $id
           OR toLower(p.name) = toLower($id))
          AND (p.organization_id = $organization_id OR p.organization_id IN $public_orgs)
        RETURN p.pathway_id AS pathway_id
        """,
        {"id": pathway_id, **scope_params(scope)},
    )

    if not pathway_check:
        raise HTTPException(status_code=404, detail=f"Pathway '{pathway_id}' not found")

    query = """
    MATCH (m:Compound)-[:PARTICIPATES_IN]->(p:Pathway)
    WHERE (p.pathway_id = $id
       OR p.kegg_id = $id
       OR toLower(p.name) = toLower($id))
      AND (p.organization_id = $organization_id OR p.organization_id IN $public_orgs)
      AND (m.organization_id = $organization_id OR m.organization_id IN $public_orgs)
    RETURN m.name AS name,
           coalesce(m.compound_id, m.metabolite_id) AS id,
           m.hmdb_id AS hmdb_id,
           m.kegg_id AS kegg_id,
           m.category AS category
    ORDER BY m.name
    SKIP $offset
    LIMIT $limit
    """

    params = {
        "id": pathway_id,
        "offset": offset,
        "limit": limit,
    }
    params.update(scope_params(scope))
    results = kg.execute_cypher(query, params)

    metabolites = [
        {
            "name": r.get("name"),
            "id": r.get("id"),
            "hmdb_id": r.get("hmdb_id"),
            "kegg_id": r.get("kegg_id"),
            "category": r.get("category"),
        }
        for r in results
    ]

    return {
        "pathway_id": pathway_id,
        "metabolites": metabolites,
        "count": len(metabolites),
    }


@router.get("/pathways/{pathway_id}/genes")
async def get_pathway_genes(
    pathway_id: str,
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Get all genes that participate in a specific pathway.

    Returns genes with `PARTICIPATES_IN` relationships to this pathway.

    **Example:** `/pathways/map00010/genes`
    """
    kg = get_kg()

    # Verify pathway exists
    pathway_check = kg.execute_cypher(
        """
        MATCH (p:Pathway)
        WHERE (p.pathway_id = $id
           OR p.kegg_id = $id
           OR toLower(p.name) = toLower($id))
          AND (p.organization_id = $organization_id OR p.organization_id IN $public_orgs)
        RETURN p.pathway_id AS pathway_id
        """,
        {"id": pathway_id, **scope_params(scope)},
    )

    if not pathway_check:
        raise HTTPException(status_code=404, detail=f"Pathway '{pathway_id}' not found")

    query = """
    MATCH (g:Gene)-[:PARTICIPATES_IN]->(p:Pathway)
    WHERE (p.pathway_id = $id
       OR p.kegg_id = $id
       OR toLower(p.name) = toLower($id))
      AND (p.organization_id = $organization_id OR p.organization_id IN $public_orgs)
      AND (g.organization_id = $organization_id OR g.organization_id IN $public_orgs)
    RETURN g.name AS name,
           g.gene_id AS gene_id,
           g.symbol AS symbol,
           g.description AS description
    ORDER BY g.name
    LIMIT $limit
    """

    params = {
        "id": pathway_id,
        "limit": limit,
    }
    params.update(scope_params(scope))
    results = kg.execute_cypher(query, params)

    genes = [
        {
            "name": r.get("name"),
            "gene_id": r.get("gene_id"),
            "symbol": r.get("symbol"),
            "description": r.get("description"),
        }
        for r in results
    ]

    return {
        "pathway_id": pathway_id,
        "genes": genes,
        "count": len(genes),
    }


@router.get("/pathways/{pathway_id}/taxa")
async def get_pathway_taxa(
    pathway_id: str,
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Get taxa linked to a pathway via the metabolites they produce.

    Finds taxa connected through a two-hop relationship:
    - Taxon -[:PRODUCES]-> Compound -[:PARTICIPATES_IN]-> Pathway

    A second branch, Taxon -> Gene -> Pathway, was advertised here and removed
    in #304: it matched a relationship type written by no loader and absent
    from the graph, so it never contributed a row. Results were always
    metabolite-only while the docstring promised both, which made systematic
    incompleteness read as a complete answer.

    **Example:** `/pathways/map00010/taxa`
    """
    kg = get_kg()

    # Verify pathway exists
    pathway_check = kg.execute_cypher(
        """
        MATCH (p:Pathway)
        WHERE (p.pathway_id = $id
           OR p.kegg_id = $id
           OR toLower(p.name) = toLower($id))
          AND (p.organization_id = $organization_id OR p.organization_id IN $public_orgs)
        RETURN p.pathway_id AS pathway_id
        """,
        {"id": pathway_id, **scope_params(scope)},
    )

    if not pathway_check:
        raise HTTPException(status_code=404, detail=f"Pathway '{pathway_id}' not found")

    # Metabolite-mediated taxa. The former gene branch is gone (#304) — see the
    # docstring; it matched a relationship type that does not exist.
    query = """
    MATCH (p:Pathway)
    WHERE (p.pathway_id = $id
       OR p.kegg_id = $id
       OR toLower(p.name) = toLower($id))
      AND (p.organization_id = $organization_id OR p.organization_id IN $public_orgs)
    MATCH (t:Taxon)-[:PRODUCES]->(m:Compound)-[:PARTICIPATES_IN]->(p)
    WHERE (t.organization_id = $organization_id OR t.organization_id IN $public_orgs)
      AND (m.organization_id = $organization_id OR m.organization_id IN $public_orgs)
    RETURN DISTINCT t.name AS name,
           t.taxon_id AS taxon_id,
           t.rank AS rank,
           ['metabolite'] AS link_types
    ORDER BY name
    LIMIT $limit
    """

    params = {
        "id": pathway_id,
        "limit": limit,
    }
    params.update(scope_params(scope))
    results = kg.execute_cypher(query, params)

    taxa = [
        {
            "name": r.get("name"),
            "taxon_id": r.get("taxon_id"),
            "rank": r.get("rank"),
            "link_types": r.get("link_types", []),
        }
        for r in results
    ]

    return {
        "pathway_id": pathway_id,
        "taxa": taxa,
        "count": len(taxa),
    }
