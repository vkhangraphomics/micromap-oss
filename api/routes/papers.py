"""
Paper-related API routes.

Provides endpoints for querying papers, searching by title/abstract,
and finding papers related to specific taxa or diseases.
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


@router.get("/papers")
async def list_papers(
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    year: Optional[int] = Query(None, description="Filter by publication year"),
    search: Optional[str] = Query(None, description="Search by title (case-insensitive)"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    List papers with optional filtering.

    Returns a paginated list of papers from the knowledge graph.
    Use filters to narrow down results by publication year or title search.

    **Examples:**
    - List recent papers: `?limit=50`
    - Filter by year: `?year=2023`
    - Search by title: `?search=microbiome`
    """
    kg = get_kg()

    # Build WHERE clauses
    where_clauses = [ORG_FILTER("p")]
    if year is not None:
        where_clauses.append("p.year = $year")
    if search:
        where_clauses.append("p.title =~ $search_pattern")

    where_clause = "WHERE " + " AND ".join(where_clauses)

    count_query = f"""
    MATCH (p:Paper)
    {where_clause}
    RETURN count(p) AS total
    """

    query = f"""
    MATCH (p:Paper)
    {where_clause}
    RETURN p.pmid AS pmid,
           p.paper_id AS paper_id,
           p.title AS title,
           p.abstract AS abstract,
           p.year AS year,
           p.journal AS journal,
           p.authors AS authors,
           p.doi AS doi
    ORDER BY p.year DESC, p.title
    SKIP $offset
    LIMIT $limit
    """

    params = {
        "year": year,
        "search_pattern": f"(?i).*{search}.*" if search else None,
        "offset": offset,
        "limit": limit,
    }
    params.update(scope_params(scope))

    count_result = kg.execute_cypher(count_query, params)
    total = count_result[0]["total"] if count_result else 0

    results = kg.execute_cypher(query, params)

    papers = [
        {
            "pmid": r.get("pmid"),
            "paper_id": r.get("paper_id"),
            "title": r.get("title"),
            "abstract": r.get("abstract"),
            "year": r.get("year"),
            "journal": r.get("journal"),
            "authors": r.get("authors"),
            "doi": r.get("doi"),
        }
        for r in results
    ]

    return {
        "papers": papers,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/papers/search/{query}")
async def search_papers(
    query: str,
    limit: int = Query(20, ge=1, le=100, description="Maximum number of results"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Search papers by title or abstract.

    Performs case-insensitive search in paper titles and abstracts.
    Results are sorted by relevance: title matches first, then abstract matches.

    **Examples:**
    - Search for microbiome papers: `/papers/search/microbiome`
    - Search for gut bacteria: `/papers/search/gut bacteria`
    """
    kg = get_kg()

    cypher = f"""
    MATCH (p:Paper)
    WHERE (p.title =~ $pattern OR p.abstract =~ $pattern)
      AND {ORG_FILTER("p")}
    WITH p,
         CASE
             WHEN toLower(p.title) = toLower($query) THEN 0
             WHEN toLower(p.title) STARTS WITH toLower($query) THEN 1
             WHEN p.title =~ $pattern THEN 2
             ELSE 3
         END AS relevance
    RETURN p.pmid AS pmid,
           p.paper_id AS paper_id,
           p.title AS title,
           p.abstract AS abstract,
           p.year AS year,
           p.journal AS journal,
           p.authors AS authors,
           p.doi AS doi,
           relevance
    ORDER BY relevance, p.year DESC, p.title
    LIMIT $limit
    """

    results = kg.execute_cypher(cypher, {
        "pattern": f"(?i).*{query}.*",
        "query": query,
        "limit": limit,
        **scope_params(scope),
    })

    return {
        "query": query,
        "results": [
            {
                "pmid": r.get("pmid"),
                "paper_id": r.get("paper_id"),
                "title": r.get("title"),
                "abstract": r.get("abstract"),
                "year": r.get("year"),
                "journal": r.get("journal"),
                "authors": r.get("authors"),
                "doi": r.get("doi"),
            }
            for r in results
        ],
        "count": len(results),
    }


@router.get("/papers/taxon/{taxon_id}")
async def get_papers_by_taxon(
    taxon_id: str,
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Get papers mentioning a specific taxon.

    Returns papers linked to the taxon via `MENTIONED_IN` relationships.

    **Accepted taxon_id formats:**
    - NCBI Taxonomy ID: `239935`
    - Prefixed ID: `NCBITaxon:239935`
    - Scientific name: `Akkermansia muciniphila`

    **Example:** `/papers/taxon/Akkermansia muciniphila`
    """
    kg = get_kg()

    # Normalize taxon_id - handle prefixed IDs
    search_id = taxon_id
    prefixed_id = taxon_id if taxon_id.startswith("NCBITaxon:") else f"NCBITaxon:{taxon_id}"

    query = f"""
    MATCH (t:Taxon)-[:MENTIONED_IN]->(p:Paper)
    WHERE (t.taxon_id = $prefixed_id
       OR t.ncbi_tax_id = $search_id
       OR t.name = $search_id
       OR toLower(t.name) = toLower($search_id))
      AND {ORG_FILTER("t")}
      AND {ORG_FILTER("p")}
    RETURN p.pmid AS pmid,
           p.paper_id AS paper_id,
           p.title AS title,
           p.abstract AS abstract,
           p.year AS year,
           p.journal AS journal,
           p.authors AS authors,
           p.doi AS doi
    ORDER BY p.year DESC, p.title
    SKIP $offset
    LIMIT $limit
    """

    results = kg.execute_cypher(query, {
        "search_id": search_id,
        "prefixed_id": prefixed_id,
        "offset": offset,
        "limit": limit,
        **scope_params(scope),
    })

    return {
        "taxon_id": taxon_id,
        "papers": [
            {
                "pmid": r.get("pmid"),
                "paper_id": r.get("paper_id"),
                "title": r.get("title"),
                "abstract": r.get("abstract"),
                "year": r.get("year"),
                "journal": r.get("journal"),
                "authors": r.get("authors"),
                "doi": r.get("doi"),
            }
            for r in results
        ],
        "count": len(results),
    }


@router.get("/papers/disease/{disease_id}")
async def get_papers_by_disease(
    disease_id: str,
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Get papers related to a specific disease.

    Returns papers linked to the disease via `MENTIONED_IN` relationships.

    **Accepted disease_id formats:**
    - Normalized name: `type_2_diabetes`
    - Exact name: `Type 2 Diabetes`

    **Example:** `/papers/disease/obesity`
    """
    kg = get_kg()

    query = f"""
    MATCH (d:Disease)-[:MENTIONED_IN]->(p:Paper)
    WHERE (d.name_normalized = $disease_id
       OR d.name = $disease_id
       OR toLower(d.name) = toLower($disease_id))
      AND {ORG_FILTER("d")}
      AND {ORG_FILTER("p")}
    RETURN p.pmid AS pmid,
           p.paper_id AS paper_id,
           p.title AS title,
           p.abstract AS abstract,
           p.year AS year,
           p.journal AS journal,
           p.authors AS authors,
           p.doi AS doi
    ORDER BY p.year DESC, p.title
    SKIP $offset
    LIMIT $limit
    """

    results = kg.execute_cypher(query, {
        "disease_id": disease_id,
        "offset": offset,
        "limit": limit,
        **scope_params(scope),
    })

    return {
        "disease_id": disease_id,
        "papers": [
            {
                "pmid": r.get("pmid"),
                "paper_id": r.get("paper_id"),
                "title": r.get("title"),
                "abstract": r.get("abstract"),
                "year": r.get("year"),
                "journal": r.get("journal"),
                "authors": r.get("authors"),
                "doi": r.get("doi"),
            }
            for r in results
        ],
        "count": len(results),
    }


@router.get("/papers/{pmid}")
async def get_paper(pmid: str, scope: OrgScope = Depends(resolve_org_scope)):
    """
    Get detailed information about a specific paper.

    Returns the paper details along with all entities (taxa, diseases, metabolites)
    that are mentioned in the paper via `MENTIONED_IN` relationships.

    **Accepted identifiers:**
    - PubMed ID: `12345678`
    - Internal paper ID

    **Example:** `/papers/12345678`
    """
    kg = get_kg()

    # Get paper details
    paper_query = f"""
    MATCH (p:Paper)
    WHERE (p.pmid = $pmid OR p.paper_id = $paper_id)
      AND {ORG_FILTER("p")}
    RETURN p.pmid AS pmid,
           p.paper_id AS paper_id,
           p.title AS title,
           p.abstract AS abstract,
           p.year AS year,
           p.journal AS journal,
           p.authors AS authors,
           p.doi AS doi
    LIMIT 1
    """

    paper_results = kg.execute_cypher(paper_query, {
        "pmid": pmid,
        "paper_id": pmid,
        **scope_params(scope),
    })

    if not paper_results:
        raise HTTPException(status_code=404, detail=f"Paper '{pmid}' not found")

    paper = paper_results[0]

    # Get mentioned taxa
    taxa_query = f"""
    MATCH (t:Taxon)-[:MENTIONED_IN]->(p:Paper)
    WHERE (p.pmid = $pmid OR p.paper_id = $paper_id)
      AND {ORG_FILTER("t")}
      AND {ORG_FILTER("p")}
    RETURN t.taxon_id AS taxon_id,
           t.ncbi_tax_id AS ncbi_tax_id,
           t.name AS name,
           t.rank AS rank
    ORDER BY t.name
    """

    # Get mentioned diseases
    diseases_query = f"""
    MATCH (d:Disease)-[:MENTIONED_IN]->(p:Paper)
    WHERE (p.pmid = $pmid OR p.paper_id = $paper_id)
      AND {ORG_FILTER("d")}
      AND {ORG_FILTER("p")}
    RETURN d.name AS name,
           d.name_normalized AS disease_id
    ORDER BY d.name
    """

    # Get mentioned metabolites
    metabolites_query = f"""
    MATCH (m:Compound)-[:MENTIONED_IN]->(p:Paper)
    WHERE (p.pmid = $pmid OR p.paper_id = $paper_id)
      AND {ORG_FILTER("m")}
      AND {ORG_FILTER("p")}
    RETURN m.name AS name,
           coalesce(m.compound_id, m.metabolite_id) AS metabolite_id
    ORDER BY m.name
    """

    entity_params = {"pmid": pmid, "paper_id": pmid, **scope_params(scope)}

    taxa_results = kg.execute_cypher(taxa_query, entity_params)
    diseases_results = kg.execute_cypher(diseases_query, entity_params)
    metabolites_results = kg.execute_cypher(metabolites_query, entity_params)

    return {
        "pmid": paper.get("pmid"),
        "paper_id": paper.get("paper_id"),
        "title": paper.get("title"),
        "abstract": paper.get("abstract"),
        "year": paper.get("year"),
        "journal": paper.get("journal"),
        "authors": paper.get("authors"),
        "doi": paper.get("doi"),
        "entities": {
            "taxa": [
                {
                    "taxon_id": r.get("taxon_id"),
                    "ncbi_tax_id": r.get("ncbi_tax_id"),
                    "name": r.get("name"),
                    "rank": r.get("rank"),
                }
                for r in taxa_results
            ],
            "diseases": [
                {
                    "name": r.get("name"),
                    "disease_id": r.get("disease_id"),
                }
                for r in diseases_results
            ],
            "metabolites": [
                {
                    "name": r.get("name"),
                    "metabolite_id": r.get("metabolite_id"),
                }
                for r in metabolites_results
            ],
        },
    }
