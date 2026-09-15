"""
Metabolite-related API routes.

Provides endpoints for querying metabolite data and their producers.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from api.scoping import OrgScope, ORG_FILTER, EDGE_ORG_FILTER, scope_params, resolve_org_scope
from api.models import (
    MetabolitesListResponse,
    MetaboliteSummary,
    MetaboliteDetail,
    MetaboliteProducersResponse,
    MetaboliteProducer,
)
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


@router.get("/metabolites", response_model=MetabolitesListResponse)
async def list_metabolites(
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    search: Optional[str] = Query(None, description="Search by metabolite name"),
    category: Optional[str] = Query(None, description="Filter by metabolite category"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    List all metabolites in the knowledge graph.

    Returns metabolites with their producer counts (number of taxa that produce them).

    **Metabolite categories:**
    - **SCFAs**: butyrate, propionate, acetate, valerate
    - **Amino acid metabolites**: GABA, tryptamine, tyramine, indole
    - **Tryptophan derivatives**: indole-3-lactic acid, indole-3-propionic acid
    - **Vitamins**: B12, folate, K2, riboflavin, biotin
    - **Bile acids**: deoxycholic acid, lithocholic acid
    - **Gases**: methane, hydrogen sulfide, hydrogen
    - **Uremic toxins**: TMA, p-cresol, phenol

    **Examples:**
    - Search for butyrate: `?search=butyrate`
    - List all SCFAs: `?category=SCFA` (if categorized)
    """
    kg = get_kg()

    # Build WHERE clauses
    where_clauses = []
    if search:
        where_clauses.append("m.name =~ $search_pattern")
    if category:
        where_clauses.append("m.category = $category")
    # Org-scope filter is always present (#200)
    where_clauses.append(ORG_FILTER("m"))

    where_clause = "WHERE " + " AND ".join(where_clauses)

    count_query = f"""
    MATCH (m:Compound)
    {where_clause}
    RETURN count(m) AS total
    """

    query = f"""
    MATCH (m:Compound)
    {where_clause}
    OPTIONAL MATCH (t:Taxon)-[:PRODUCES]->(m)
    WHERE {ORG_FILTER("t")}
    WITH m, count(DISTINCT t) AS producer_count
    RETURN m.name AS name,
           coalesce(m.compound_id, m.metabolite_id) AS id,
           m.hmdb_id AS hmdb_id,
           m.kegg_id AS kegg_id,
           m.category AS category,
           producer_count
    ORDER BY m.name
    SKIP $offset
    LIMIT $limit
    """

    params = {
        "search_pattern": f"(?i).*{search}.*" if search else None,
        "category": category,
        "offset": offset,
        "limit": limit,
    }
    params.update(scope_params(scope))

    count_result = kg.execute_cypher(count_query, params)
    total = count_result[0]["total"] if count_result else 0

    results = kg.execute_cypher(query, params)

    metabolites = [
        MetaboliteSummary(
            id=r.get("id"),
            name=r["name"],
            hmdb_id=r.get("hmdb_id"),
            kegg_id=r.get("kegg_id"),
            category=r.get("category"),
            producer_count=r["producer_count"],
        )
        for r in results
    ]

    return MetabolitesListResponse(
        metabolites=metabolites,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/metabolites/{metabolite_id}", response_model=MetaboliteDetail)
async def get_metabolite(
    metabolite_id: str,
    top_producers_limit: int = Query(10, ge=1, le=100, description="Number of top producers to include"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Get detailed information about a specific metabolite.

    **Accepted metabolite_id formats:**
    - Name: `butyrate`, `GABA`, `indole`
    - HMDB ID: `HMDB0000123` (if available)
    - KEGG ID: `C00001` (if available)

    Returns metabolite details including top producer taxa. Producers are
    taxa known to synthesize this metabolite based on curated literature.

    **Example:** Butyrate is produced by Faecalibacterium, Roseburia, Eubacterium, and others.
    """
    kg = get_kg()

    query = """
    MATCH (m:Compound)
    WHERE (m.compound_id = $metabolite_id
       OR m.metabolite_id = $metabolite_id
       OR m.hmdb_id = $metabolite_id
       OR m.kegg_id = $metabolite_id
       OR m.name = $metabolite_id
       OR toLower(m.name) = toLower($metabolite_id)
       OR m.name_lower = toLower($metabolite_id))
      AND (m.organization_id = $organization_id OR m.organization_id IN $public_orgs)
    OPTIONAL MATCH (t:Taxon)-[:PRODUCES]->(m)
    WHERE (t.organization_id = $organization_id OR t.organization_id IN $public_orgs)
    WITH m, t
    ORDER BY t.name
    WITH m,
         count(DISTINCT t) AS producer_count,
         collect({
             name: t.name,
             id: t.ncbi_id,
             rank: t.rank
         })[0..$top_producers_limit] AS top_producers
    RETURN m.name AS name,
           coalesce(m.compound_id, m.metabolite_id) AS id,
           m.hmdb_id AS hmdb_id,
           m.kegg_id AS kegg_id,
           m.category AS category,
           m.description AS description,
           producer_count,
           top_producers
    """

    params = {
        "metabolite_id": metabolite_id,
        "top_producers_limit": top_producers_limit,
    }
    params.update(scope_params(scope))
    results = kg.execute_cypher(query, params)

    if not results:
        raise HTTPException(status_code=404, detail=f"Metabolite '{metabolite_id}' not found")

    r = results[0]
    return MetaboliteDetail(
        id=r.get("id"),
        name=r["name"],
        hmdb_id=r.get("hmdb_id"),
        kegg_id=r.get("kegg_id"),
        category=r.get("category"),
        description=r.get("description"),
        producer_count=r["producer_count"],
        top_producers=[p for p in r.get("top_producers", []) if p.get("name")],
    )


@router.get("/metabolites/{metabolite_id}/producers", response_model=MetaboliteProducersResponse)
async def get_metabolite_producers(
    metabolite_id: str,
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    rank: Optional[str] = Query(None, description="Filter by taxonomic rank"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Get all taxa that produce a specific metabolite.

    Returns taxa with `PRODUCES` relationships to this metabolite,
    based on curated scientific literature.

    **Use cases:**
    - Find probiotic candidates that produce beneficial metabolites
    - Identify SCFA producers for gut health research
    - Discover vitamin-producing bacteria

    **Example:** `/metabolites/butyrate/producers` returns Faecalibacterium prausnitzii,
    Roseburia intestinalis, Eubacterium rectale, and other known butyrate producers.
    """
    kg = get_kg()

    # First verify metabolite exists
    metabolite_check = kg.execute_cypher(
        """
        MATCH (m:Compound)
        WHERE (m.compound_id = $metabolite_id
           OR m.metabolite_id = $metabolite_id
           OR m.hmdb_id = $metabolite_id
           OR m.kegg_id = $metabolite_id
           OR m.name = $metabolite_id
           OR toLower(m.name) = toLower($metabolite_id)
           OR m.name_lower = toLower($metabolite_id))
          AND (m.organization_id = $organization_id OR m.organization_id IN $public_orgs)
        RETURN m.name AS name
        """,
        {"metabolite_id": metabolite_id, **scope_params(scope)},
    )

    if not metabolite_check:
        raise HTTPException(status_code=404, detail=f"Metabolite '{metabolite_id}' not found")

    # Build WHERE clause with optional rank filter
    where_clauses = [
        "(m.compound_id = $metabolite_id"
        " OR m.metabolite_id = $metabolite_id"
        " OR m.hmdb_id = $metabolite_id"
        " OR m.kegg_id = $metabolite_id"
        " OR m.name = $metabolite_id"
        " OR toLower(m.name) = toLower($metabolite_id)"
        " OR m.name_lower = toLower($metabolite_id))"
    ]
    where_clauses.append(ORG_FILTER("m"))
    where_clauses.append(ORG_FILTER("t"))
    where_clauses.append(EDGE_ORG_FILTER("r"))  # #297: scope the contributed PRODUCES edge
    if rank:
        where_clauses.append("t.rank = $rank")

    where_clause = "WHERE " + " AND ".join(where_clauses)

    # `r` is bound in both queries so the shared where_clause can scope it (#297).
    count_query = f"""
    MATCH (t:Taxon)-[r:PRODUCES]->(m:Compound)
    {where_clause}
    RETURN count(t) AS total
    """

    query = f"""
    MATCH (t:Taxon)-[r:PRODUCES]->(m:Compound)
    {where_clause}
    RETURN t.name AS taxon_name,
           t.ncbi_tax_id AS ncbi_id,
           t.taxon_id AS taxon_id,
           t.rank AS rank,
           m.name AS metabolite_name,
           r.evidence_level AS evidence_level,
           r.pathway_id AS pathway_id,
           r.yield AS yield_value,
           r.notes AS notes,
           r.source AS source
    ORDER BY t.name
    SKIP $offset
    LIMIT $limit
    """

    params = {
        "metabolite_id": metabolite_id,
        "rank": rank,
        "offset": offset,
        "limit": limit,
    }
    params.update(scope_params(scope))

    count_result = kg.execute_cypher(count_query, params)
    total = count_result[0]["total"] if count_result else 0

    results = kg.execute_cypher(query, params)

    producers = [
        MetaboliteProducer(
            taxon_name=r["taxon_name"],
            taxon_id=r.get("taxon_id"),
            ncbi_id=r.get("ncbi_id"),
            rank=r.get("rank"),
            metabolite_name=r.get("metabolite_name"),
            evidence_level=r.get("evidence_level"),
            pathway_id=r.get("pathway_id"),
            yield_value=r.get("yield_value"),
            notes=r.get("notes"),
            source=r.get("source"),
        )
        for r in results
    ]

    return MetaboliteProducersResponse(
        metabolite=metabolite_id,
        producers=producers,
        count=total,
    )


@router.get("/metabolites/categories/list")
async def list_metabolite_categories(
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Get all metabolite categories in the knowledge graph.

    Returns a list of unique categories with the count of metabolites in each.
    Use this to discover available categories for filtering in other endpoints.
    """
    kg = get_kg()

    query = """
    MATCH (m:Compound)
    WHERE m.category IS NOT NULL
      AND (m.organization_id = $organization_id OR m.organization_id IN $public_orgs)
    RETURN m.category AS category, count(m) AS count
    ORDER BY count DESC, category
    """

    params = {}
    params.update(scope_params(scope))
    results = kg.execute_cypher(query, params)

    return {
        "categories": [
            {"name": r["category"], "count": r["count"]}
            for r in results
        ],
        "total": len(results),
    }


@router.get("/metabolites/{metabolite_id}/diseases")
async def get_metabolite_diseases(
    metabolite_id: str,
    limit: int = Query(100, ge=1, le=500, description="Maximum number of results"),
    min_producers: int = Query(1, ge=1, description="Minimum producing taxa associated with disease"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Get diseases linked to a metabolite via producing taxa.

    Finds all diseases where taxa that produce this metabolite are associated.
    This is a reverse lookup: metabolite → producers → diseases.

    **Use cases:**
    - Identify diseases where a metabolite may be altered
    - Find therapeutic targets for metabolite supplementation
    - Understand which diseases involve specific metabolic pathways

    **Example:** For butyrate, returns IBD, Crohn's Disease (where butyrate producers
    like Faecalibacterium are depleted), suggesting reduced butyrate in these conditions.
    """
    kg = get_kg()

    # First verify metabolite exists
    metabolite_check = kg.execute_cypher(
        """
        MATCH (m:Compound)
        WHERE (m.compound_id = $metabolite_id
           OR m.metabolite_id = $metabolite_id
           OR m.hmdb_id = $metabolite_id
           OR m.kegg_id = $metabolite_id
           OR m.name = $metabolite_id
           OR toLower(m.name) = toLower($metabolite_id)
           OR m.name_lower = toLower($metabolite_id))
          AND (m.organization_id = $organization_id OR m.organization_id IN $public_orgs)
        RETURN m.name AS name
        """,
        {"metabolite_id": metabolite_id, **scope_params(scope)},
    )

    if not metabolite_check:
        raise HTTPException(status_code=404, detail=f"Metabolite '{metabolite_id}' not found")

    # Get diseases via metabolite producers
    query = """
    MATCH (m:Compound)<-[:PRODUCES]-(t:Taxon)-[r:ASSOCIATED_WITH_DISEASE]->(d:Disease)
    WHERE (m.compound_id = $metabolite_id
       OR m.metabolite_id = $metabolite_id
       OR m.hmdb_id = $metabolite_id
       OR m.kegg_id = $metabolite_id
       OR m.name = $metabolite_id
       OR toLower(m.name) = toLower($metabolite_id)
       OR m.name_lower = toLower($metabolite_id))
      AND (m.organization_id = $organization_id OR m.organization_id IN $public_orgs)
      AND (t.organization_id = $organization_id OR t.organization_id IN $public_orgs)
      AND (d.organization_id = $organization_id OR d.organization_id IN $public_orgs)
      AND (r.organization_id IS NULL OR r.organization_id = $organization_id OR r.organization_id IN $public_orgs)
    WITH d, collect(DISTINCT {taxon: t.name, direction: r.direction}) AS producers
    WHERE size(producers) >= $min_producers
    RETURN d.name AS disease_name,
           d.name_normalized AS disease_id,
           size(producers) AS producer_count,
           size([p IN producers WHERE p.direction IN ['enriched', 'increased']]) AS enriched_count,
           size([p IN producers WHERE p.direction IN ['depleted', 'decreased']]) AS depleted_count
    ORDER BY producer_count DESC, d.name
    LIMIT $limit
    """

    params = {
        "metabolite_id": metabolite_id,
        "min_producers": min_producers,
        "limit": limit,
    }
    params.update(scope_params(scope))
    results = kg.execute_cypher(query, params)

    diseases = []
    for r in results:
        diseases.append({
            "disease_name": r["disease_name"],
            "disease_id": r.get("disease_id") or r["disease_name"].lower().replace(" ", "_"),
            "producer_count": r["producer_count"],
            "directions": {
                "enriched": r.get("enriched_count", 0),
                "depleted": r.get("depleted_count", 0),
            },
        })

    return {
        "metabolite": metabolite_id,
        "diseases": diseases,
        "count": len(diseases),
    }


@router.get("/metabolites/{metabolite_id}/pathways")
async def get_metabolite_pathways(
    metabolite_id: str,
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Get all pathways a metabolite participates in.

    Returns pathways linked via `PARTICIPATES_IN` relationships.

    **Use cases:**
    - Discover which metabolic pathways involve a given metabolite
    - Build mechanistic chains: Disease → Taxa → Metabolite → Pathway
    - Identify pathway-level context for metabolite alterations in disease

    **Example:** `/metabolites/butyrate/pathways` returns pathways like
    butanoate metabolism that involve butyrate.
    """
    kg = get_kg()

    # Verify metabolite exists
    metabolite_check = kg.execute_cypher(
        """
        MATCH (m:Compound)
        WHERE (m.compound_id = $metabolite_id
           OR m.metabolite_id = $metabolite_id
           OR m.hmdb_id = $metabolite_id
           OR m.kegg_id = $metabolite_id
           OR m.name = $metabolite_id
           OR toLower(m.name) = toLower($metabolite_id)
           OR m.name_lower = toLower($metabolite_id))
          AND (m.organization_id = $organization_id OR m.organization_id IN $public_orgs)
        RETURN m.name AS name
        """,
        {"metabolite_id": metabolite_id, **scope_params(scope)},
    )

    if not metabolite_check:
        raise HTTPException(status_code=404, detail=f"Metabolite '{metabolite_id}' not found")

    query = """
    MATCH (m:Compound)-[:PARTICIPATES_IN]->(p:Pathway)
    WHERE (m.compound_id = $metabolite_id
       OR m.metabolite_id = $metabolite_id
       OR m.hmdb_id = $metabolite_id
       OR m.kegg_id = $metabolite_id
       OR m.name = $metabolite_id
       OR toLower(m.name) = toLower($metabolite_id)
       OR m.name_lower = toLower($metabolite_id))
      AND (m.organization_id = $organization_id OR m.organization_id IN $public_orgs)
      AND (p.organization_id = $organization_id OR p.organization_id IN $public_orgs)
    RETURN p.pathway_id AS pathway_id,
           p.kegg_id AS kegg_id,
           p.name AS name,
           p.description AS description,
           p.category AS category,
           p.pathway_type AS pathway_type
    ORDER BY p.name
    SKIP $offset
    LIMIT $limit
    """

    params = {
        "metabolite_id": metabolite_id,
        "offset": offset,
        "limit": limit,
    }
    params.update(scope_params(scope))
    results = kg.execute_cypher(query, params)

    pathways = [
        {
            "pathway_id": r.get("pathway_id"),
            "kegg_id": r.get("kegg_id"),
            "name": r.get("name"),
            "description": r.get("description"),
            "category": r.get("category"),
            "pathway_type": r.get("pathway_type"),
        }
        for r in results
    ]

    return {
        "metabolite": metabolite_id,
        "pathways": pathways,
        "count": len(pathways),
    }
