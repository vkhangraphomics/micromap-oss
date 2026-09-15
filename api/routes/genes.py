"""
Gene-related API routes.

Provides endpoints for querying gene data, associated taxa,
and pathway participation.
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


@router.get("/genes")
async def list_genes(
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    search: Optional[str] = Query(None, description="Search by gene name (case-insensitive partial match)"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    List genes with optional filtering.

    Returns a paginated list of genes in the knowledge graph.

    **Examples:**
    - List all genes: `/genes`
    - Search for a gene: `/genes?search=lacZ`
    - Paginate: `/genes?limit=50&offset=100`
    """
    kg = get_kg()

    # Build WHERE clauses
    where_clauses = []
    if search:
        where_clauses.append("g.name =~ $search_pattern")
    # Org-scope filter is always present (#200)
    where_clauses.append(ORG_FILTER("g"))

    where_clause = "WHERE " + " AND ".join(where_clauses)

    count_query = f"""
    MATCH (g:Gene)
    {where_clause}
    RETURN count(g) AS total
    """

    query = f"""
    MATCH (g:Gene)
    {where_clause}
    RETURN g.name AS name,
           g.gene_id AS gene_id,
           g.symbol AS symbol,
           g.description AS description
    ORDER BY g.name
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

    genes = [
        {
            "gene_id": r.get("gene_id"),
            "name": r.get("name"),
            "symbol": r.get("symbol"),
            "description": r.get("description"),
        }
        for r in results
    ]

    return {
        "genes": genes,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/genes/search/{query}")
async def search_genes(
    query: str,
    limit: int = Query(20, ge=1, le=100, description="Maximum number of results"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Search genes by name.

    Performs case-insensitive partial matching on gene names.
    Results are sorted by relevance: exact matches first, then prefix matches, then partial matches.

    **Note:** For broader search across all entity types, use `/api/v1/search?q=query` instead.
    """
    kg = get_kg()

    cypher = """
    MATCH (g:Gene)
    WHERE g.name =~ $pattern
      AND (g.organization_id = $organization_id OR g.organization_id IN $public_orgs)
    WITH g,
         CASE WHEN toLower(g.name) = toLower($query) THEN 0
              WHEN toLower(g.name) STARTS WITH toLower($query) THEN 1
              ELSE 2
         END AS relevance
    RETURN g.gene_id AS gene_id,
           g.name AS name,
           g.symbol AS symbol,
           g.description AS description
    ORDER BY relevance, g.name
    LIMIT $limit
    """

    params = {
        "pattern": f"(?i).*{query}.*",
        "query": query,
        "limit": limit,
    }
    params.update(scope_params(scope))
    results = kg.execute_cypher(cypher, params)

    return {
        "query": query,
        "results": [
            {
                "gene_id": r.get("gene_id"),
                "name": r.get("name"),
                "symbol": r.get("symbol"),
                "description": r.get("description"),
            }
            for r in results
        ],
        "count": len(results),
    }


@router.get("/genes/{gene_id}")
async def get_gene(
    gene_id: str,
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Get detailed information about a specific gene.

    **Accepted gene_id formats:**
    - Gene ID: `12345`
    - Gene name or symbol: `lacZ`

    Returns gene details including all available properties.
    """
    kg = get_kg()

    # Normalize gene_id
    search_id = gene_id

    query = """
    MATCH (g:Gene)
    WHERE (g.gene_id = $search_id
       OR g.name = $search_id
       OR g.symbol = $search_id
       OR toLower(g.name) = toLower($search_id)
       OR toLower(g.symbol) = toLower($search_id))
      AND (g.organization_id = $organization_id OR g.organization_id IN $public_orgs)
    RETURN g.gene_id AS gene_id,
           g.name AS name,
           g.symbol AS symbol,
           g.description AS description
    LIMIT 1
    """

    params = {"search_id": search_id}
    params.update(scope_params(scope))
    results = kg.execute_cypher(query, params)

    if not results:
        raise HTTPException(status_code=404, detail=f"Gene '{gene_id}' not found")

    r = results[0]
    return {
        "gene_id": r.get("gene_id"),
        "name": r.get("name"),
        "symbol": r.get("symbol"),
        "description": r.get("description"),
    }


@router.get("/genes/{gene_id}/taxa")
async def get_gene_taxa(
    gene_id: str,
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Not available: the graph holds no taxon-gene linkage.

    This endpoint previously matched `(:Gene)<-[:HAS_GENE]-(:Taxon)` and
    returned `{"taxa": [], "count": 0}` with a 200 for every gene, because
    `HAS_GENE` is written by no loader and does not exist in the graph. An
    empty 200 is a claim — "no taxon carries this gene" — and we cannot
    support it, so the endpoint now refuses instead (#304).

    This is not a rename away from working. `:Gene` nodes are derived from
    `Protein.gene_name` (#251) and their only edge is
    `(:Protein)-[:ENCODED_BY]->(:Gene)`; `:Protein` carries no `:Taxon` edge,
    so there is no taxon-gene path of any length to re-point the query at.
    Serving this needs a real taxon-gene source ingested first.
    """
    raise HTTPException(
        status_code=501,
        detail=(
            "Taxon-gene links are not available: no taxon-gene relationship is "
            "loaded in the knowledge graph. Gene nodes are derived from "
            "Protein.gene_name and connect only via "
            "(:Protein)-[:ENCODED_BY]->(:Gene). See issue #304."
        ),
    )


@router.get("/genes/{gene_id}/pathways")
async def get_gene_pathways(
    gene_id: str,
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Get pathways this gene participates in.

    Returns pathways linked to the specified gene via `PARTICIPATES_IN` relationships.

    **Example:** `/genes/lacZ/pathways` returns metabolic pathways involving the lacZ gene.
    """
    kg = get_kg()

    # Normalize gene_id
    search_id = gene_id

    # First verify gene exists
    gene_check = kg.execute_cypher(
        """
        MATCH (g:Gene)
        WHERE (g.gene_id = $search_id
           OR g.name = $search_id
           OR g.symbol = $search_id
           OR toLower(g.name) = toLower($search_id)
           OR toLower(g.symbol) = toLower($search_id))
          AND (g.organization_id = $organization_id OR g.organization_id IN $public_orgs)
        RETURN g.gene_id AS gene_id, g.name AS name
        LIMIT 1
        """,
        {"search_id": search_id, **scope_params(scope)},
    )

    if not gene_check:
        raise HTTPException(status_code=404, detail=f"Gene '{gene_id}' not found")

    query = """
    MATCH (g:Gene)-[:PARTICIPATES_IN]->(p:Pathway)
    WHERE (g.gene_id = $search_id
       OR g.name = $search_id
       OR g.symbol = $search_id
       OR toLower(g.name) = toLower($search_id)
       OR toLower(g.symbol) = toLower($search_id))
      AND (g.organization_id = $organization_id OR g.organization_id IN $public_orgs)
      AND (p.organization_id = $organization_id OR p.organization_id IN $public_orgs)
    RETURN p.name AS pathway_name,
           p.pathway_id AS pathway_id,
           p.description AS description
    ORDER BY p.name
    SKIP $offset
    LIMIT $limit
    """

    params = {
        "search_id": search_id,
        "offset": offset,
        "limit": limit,
    }
    params.update(scope_params(scope))
    results = kg.execute_cypher(query, params)

    pathways = [
        {
            "pathway_name": r.get("pathway_name"),
            "pathway_id": r.get("pathway_id"),
            "description": r.get("description"),
        }
        for r in results
    ]

    return {
        "gene_id": gene_id,
        "pathways": pathways,
        "count": len(pathways),
    }
