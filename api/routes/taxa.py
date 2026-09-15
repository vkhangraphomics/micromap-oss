"""
Taxa-related API routes.

Provides endpoints for querying taxonomic data, taxonomic hierarchy,
associated diseases, and metabolite production.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from typing import Optional

from api.scoping import OrgScope, ORG_FILTER, EDGE_ORG_FILTER, scope_params, resolve_org_scope
from api.models import (
    TaxaListResponse,
    TaxonDetail,
    TaxonChildrenResponse,
    TaxonDiseasesResponse,
    TaxonDiseaseAssociation,
    TaxonMetabolitesResponse,
    TaxonMetabolite,
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


@router.get("/taxa/compare")
async def compare_taxa(
    ids: str = Query(..., description="Comma-separated taxon IDs or names to compare"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Compare metabolite production across multiple taxa.

    Returns the metabolites produced by each taxon, plus shared metabolites
    produced by all of them.

    **Example:** `/taxa/compare?ids=Faecalibacterium,Roseburia,Eubacterium`

    Useful for:
    - Identifying common metabolic outputs across related bacteria
    - Finding unique metabolite producers
    - Comparing probiotic candidates
    """
    kg = get_kg()

    taxon_list = [t.strip() for t in ids.split(",") if t.strip()]
    if len(taxon_list) < 2:
        raise HTTPException(status_code=400, detail="At least 2 taxa required for comparison")
    if len(taxon_list) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 taxa for comparison")

    profiles = []
    all_metabolites_sets = []

    for taxon_id in taxon_list:
        search_id = taxon_id
        prefixed_id = taxon_id if taxon_id.startswith("NCBITaxon:") else f"NCBITaxon:{taxon_id}"

        query = """
        MATCH (t:Taxon)
        WHERE (t.taxon_id = $prefixed_id
           OR t.ncbi_tax_id = $search_id
           OR t.name = $search_id
           OR toLower(t.name) = toLower($search_id)
           OR t.name STARTS WITH $search_id)
          AND (t.organization_id = $organization_id OR t.organization_id IN $public_orgs)
        OPTIONAL MATCH (t)-[:PRODUCES]->(m:Compound)
        WHERE (m.organization_id = $organization_id OR m.organization_id IN $public_orgs)
        WITH t, collect(DISTINCT m.name) AS metabolites
        RETURN t.taxon_id AS taxon_id,
               t.name AS taxon_name,
               metabolites
        LIMIT 1
        """

        params = {
            "search_id": search_id,
            "prefixed_id": prefixed_id,
        }
        params.update(scope_params(scope))
        results = kg.execute_cypher(query, params)

        if results:
            r = results[0]
            metabolites = [m for m in r.get("metabolites", []) if m]
            profiles.append({
                "taxon_id": r.get("taxon_id") or taxon_id,
                "taxon_name": r.get("taxon_name") or taxon_id,
                "metabolites": metabolites,
                "count": len(metabolites),
            })
            all_metabolites_sets.append(set(metabolites))
        else:
            profiles.append({
                "taxon_id": taxon_id,
                "taxon_name": taxon_id,
                "metabolites": [],
                "count": 0,
            })
            all_metabolites_sets.append(set())

    # Calculate shared and all metabolites
    if all_metabolites_sets:
        shared = set.intersection(*all_metabolites_sets) if all_metabolites_sets else set()
        all_unique = set.union(*all_metabolites_sets) if all_metabolites_sets else set()
    else:
        shared = set()
        all_unique = set()

    return {
        "taxa": profiles,
        "shared_metabolites": sorted(shared),
        "all_metabolites": sorted(all_unique),
    }


@router.get("/taxa", response_model=TaxaListResponse)
async def list_taxa(
    rank: Optional[str] = Query(
        None,
        description="Filter by taxonomic rank: 'domain', 'phylum', 'class', 'order', 'family', 'genus', 'species'",
    ),
    kingdom: Optional[str] = Query(None, description="Filter by kingdom (e.g., 'Bacteria', 'Archaea')"),
    search: Optional[str] = Query(None, description="Search by name (case-insensitive partial match)"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    List taxa with optional filtering.

    Returns a paginated list of taxa from the NCBI Taxonomy database.
    Use filters to narrow down results by rank, kingdom, or name search.

    **Examples:**
    - List all genera: `?rank=genus&limit=100`
    - Search for Lactobacillus: `?search=lactobacillus`
    - List Bacteria species: `?kingdom=Bacteria&rank=species`

    For taxonomic hierarchy navigation, use the `/taxa/{id}/children` endpoint.
    """
    kg = get_kg()

    # Build WHERE clauses
    where_clauses = []
    if rank:
        where_clauses.append("t.rank = $rank")
    if kingdom:
        where_clauses.append("t.kingdom = $kingdom")
    if search:
        where_clauses.append("t.name =~ $search_pattern")
    # Org-scope filter is always present (#200)
    where_clauses.append(ORG_FILTER("t"))

    where_clause = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    count_query = f"""
    MATCH (t:Taxon)
    {where_clause}
    RETURN count(t) AS total
    """

    query = f"""
    MATCH (t:Taxon)
    {where_clause}
    RETURN t.ncbi_tax_id AS ncbi_tax_id,
           t.taxon_id AS taxon_id,
           t.name AS name,
           t.rank AS rank,
           t.kingdom AS kingdom,
           t.phylum AS phylum,
           t.family AS family,
           t.genus AS genus
    ORDER BY t.name
    SKIP $offset
    LIMIT $limit
    """

    params = {
        "rank": rank,
        "kingdom": kingdom,
        "search_pattern": f"(?i).*{search}.*" if search else None,
        "offset": offset,
        "limit": limit,
    }
    params.update(scope_params(scope))

    count_result = kg.execute_cypher(count_query, params)
    total = count_result[0]["total"] if count_result else 0

    results = kg.execute_cypher(query, params)

    taxa = [
        {
            "taxon_id": r.get("taxon_id"),
            "ncbi_tax_id": r.get("ncbi_tax_id"),
            "name": r["name"],
            "rank": r.get("rank"),
            "kingdom": r.get("kingdom"),
            "phylum": r.get("phylum"),
            "family": r.get("family"),
            "genus": r.get("genus"),
        }
        for r in results
    ]

    return TaxaListResponse(
        taxa=taxa,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/taxa/{taxon_id}", response_model=TaxonDetail)
async def get_taxon(taxon_id: str, scope: OrgScope = Depends(resolve_org_scope)):
    """
    Get detailed information about a specific taxon.

    **Accepted taxon_id formats:**
    - NCBI Taxonomy ID: `239935`
    - Prefixed ID: `NCBITaxon:239935`
    - Scientific name: `Akkermansia muciniphila`

    **Example response:**
    ```json
    {
      "taxon_id": "NCBITaxon:239935",
      "name": "Akkermansia muciniphila",
      "rank": "species",
      "kingdom": "Bacteria",
      "phylum": "Verrucomicrobia",
      "genus": "Akkermansia"
    }
    ```
    """
    kg = get_kg()

    # Normalize taxon_id - handle prefixed IDs
    search_id = taxon_id
    prefixed_id = taxon_id if taxon_id.startswith("NCBITaxon:") else f"NCBITaxon:{taxon_id}"

    query = """
    MATCH (t:Taxon)
    WHERE (t.taxon_id = $prefixed_id
       OR t.ncbi_tax_id = $search_id
       OR t.name = $search_id
       OR toLower(t.name) = toLower($search_id))
      AND (t.organization_id = $organization_id OR t.organization_id IN $public_orgs)
    RETURN t.ncbi_tax_id AS ncbi_tax_id,
           t.taxon_id AS taxon_id,
           t.name AS name,
           t.common_name AS common_name,
           t.rank AS rank,
           t.kingdom AS kingdom,
           t.phylum AS phylum,
           t.class AS class_,
           t.order AS order,
           t.family AS family,
           t.genus AS genus,
           t.species AS species
    """

    params = {"search_id": search_id, "prefixed_id": prefixed_id}
    params.update(scope_params(scope))
    results = kg.execute_cypher(query, params)

    if not results:
        raise HTTPException(status_code=404, detail=f"Taxon '{taxon_id}' not found")

    r = results[0]
    return TaxonDetail(
        taxon_id=r.get("taxon_id"),
        ncbi_tax_id=r.get("ncbi_tax_id"),
        name=r["name"],
        common_name=r.get("common_name"),
        rank=r.get("rank"),
        kingdom=r.get("kingdom"),
        phylum=r.get("phylum"),
        class_=r.get("class_"),
        order=r.get("order"),
        family=r.get("family"),
        genus=r.get("genus"),
        species=r.get("species"),
    )


@router.get("/taxa/{taxon_id}/children", response_model=TaxonChildrenResponse)
async def get_taxon_children(
    taxon_id: str,
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of children to return"),
    rank: Optional[str] = Query(None, description="Filter children by taxonomic rank"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Get child taxa in the taxonomic hierarchy.

    Returns immediate children of the specified taxon via `HAS_PARENT` relationships.
    Use this to navigate down the taxonomic tree (e.g., from genus to species).

    **Examples:**
    - Get species under Lactobacillus: `/taxa/Lactobacillus/children?rank=species`
    - Get phyla under Bacteria: `/taxa/Bacteria/children?rank=phylum`
    """
    kg = get_kg()

    # Normalize taxon_id - handle prefixed IDs
    search_id = taxon_id
    prefixed_id = taxon_id if taxon_id.startswith("NCBITaxon:") else f"NCBITaxon:{taxon_id}"

    # First verify parent exists
    parent_check = kg.execute_cypher(
        """
        MATCH (t:Taxon)
        WHERE (t.taxon_id = $prefixed_id OR t.ncbi_tax_id = $search_id OR t.name = $search_id)
          AND (t.organization_id = $organization_id OR t.organization_id IN $public_orgs)
        RETURN t.taxon_id AS id, t.name AS name
        """,
        {"search_id": search_id, "prefixed_id": prefixed_id, **scope_params(scope)},
    )

    if not parent_check:
        raise HTTPException(status_code=404, detail=f"Taxon '{taxon_id}' not found")

    # Build query with optional rank filter
    where_clause = ""
    if rank:
        where_clause = "AND child.rank = $rank"

    query = f"""
    MATCH (parent:Taxon)<-[:HAS_PARENT]-(child:Taxon)
    WHERE (parent.taxon_id = $prefixed_id OR parent.ncbi_tax_id = $search_id OR parent.name = $search_id)
      AND (parent.organization_id = $organization_id OR parent.organization_id IN $public_orgs)
      AND (child.organization_id = $organization_id OR child.organization_id IN $public_orgs)
    {where_clause}
    RETURN child.ncbi_tax_id AS ncbi_id,
           child.taxon_id AS id,
           child.name AS name,
           child.rank AS rank
    ORDER BY child.name
    LIMIT $limit
    """

    results = kg.execute_cypher(query, {
        "search_id": search_id,
        "prefixed_id": prefixed_id,
        "rank": rank,
        "limit": limit,
        **scope_params(scope),
    })

    children = [
        {
            "id": r.get("id"),
            "ncbi_id": r.get("ncbi_id"),
            "name": r["name"],
            "rank": r.get("rank"),
        }
        for r in results
    ]

    return TaxonChildrenResponse(
        parent_id=taxon_id,
        children=children,
        count=len(children),
    )


@router.get("/taxa/{taxon_id}/diseases")
async def get_taxon_diseases(
    taxon_id: str,
    request: Request,
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    direction: Optional[str] = Query(
        None,
        description="Filter by direction: 'increased', 'decreased', or 'unchanged'",
    ),
    evidence_level: Optional[str] = Query(
        None,
        description="Filter by evidence level: 'high', 'medium', or 'low'",
    ),
    sort_by: Optional[str] = Query(
        None,
        description="Sort results by: 'effect_size', 'p_value', or 'n_studies' (default: name)",
    ),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Get diseases associated with a taxon.

    Returns diseases where this taxon has been implicated in scientific literature,
    along with the direction of association:
    - **enriched**: Taxon abundance is increased in the disease
    - **depleted**: Taxon abundance is decreased in the disease
    - **altered**: Direction varies or is unclear

    **Example:** Akkermansia muciniphila is depleted in obesity, T2D, and IBD.
    """
    kg = get_kg()

    # Normalize taxon_id - handle prefixed IDs
    search_id = taxon_id
    prefixed_id = taxon_id if taxon_id.startswith("NCBITaxon:") else f"NCBITaxon:{taxon_id}"

    # First verify taxon exists
    taxon_check = kg.execute_cypher(
        """
        MATCH (t:Taxon)
        WHERE (t.taxon_id = $prefixed_id OR t.ncbi_tax_id = $search_id OR t.name = $search_id)
          AND (t.organization_id = $organization_id OR t.organization_id IN $public_orgs)
        RETURN t.taxon_id AS id, t.name AS name
        """,
        {"search_id": search_id, "prefixed_id": prefixed_id, **scope_params(scope)},
    )

    if not taxon_check:
        raise HTTPException(status_code=404, detail=f"Taxon '{taxon_id}' not found")

    # Build query with optional direction and evidence_level filters
    where_clause = (
        "WHERE (t.taxon_id = $prefixed_id OR t.ncbi_tax_id = $search_id OR t.name = $search_id)"
        "\n      AND (t.organization_id = $organization_id OR t.organization_id IN $public_orgs)"
        "\n      AND (d.organization_id = $organization_id OR d.organization_id IN $public_orgs)"
        "\n      AND " + EDGE_ORG_FILTER("r")  # #297: scope the contributed AWD edge
    )
    if direction:
        where_clause += " AND r.direction = $direction"
    if evidence_level:
        where_clause += " AND r.evidence_level = $evidence_level"

    # Determine sort order
    sort_clause = "ORDER BY d.name"
    if sort_by == "effect_size":
        sort_clause = "ORDER BY r.effect_size DESC"
    elif sort_by == "p_value":
        sort_clause = "ORDER BY r.p_value ASC"
    elif sort_by == "n_studies":
        sort_clause = "ORDER BY r.n_studies DESC"

    query = f"""
    MATCH (t:Taxon)-[r:ASSOCIATED_WITH_DISEASE]->(d:Disease)
    {where_clause}
    RETURN d.name AS disease_name,
           d.name_normalized AS disease_id,
           r.direction AS direction,
           r.qualitative_outcome AS outcome,
           collect(DISTINCT r.source) AS sources,
           r.effect_size AS effect_size,
           r.p_value AS p_value,
           r.evidence_level AS evidence_level,
           r.n_studies AS n_studies,
           r.pmids AS pmids
    {sort_clause}
    LIMIT $limit
    """

    results = kg.execute_cypher(query, {
        "search_id": search_id,
        "prefixed_id": prefixed_id,
        "direction": direction,
        "evidence_level": evidence_level,
        "limit": limit,
        **scope_params(scope),
    })

    diseases = [
        TaxonDiseaseAssociation(
            disease_name=r["disease_name"],
            disease_id=r.get("disease_id"),
            direction=r.get("direction"),
            outcome=r.get("outcome"),
            sources=[s for s in (r.get("sources") or []) if s] or None,
            effect_size=r.get("effect_size"),
            p_value=r.get("p_value"),
            evidence_level=r.get("evidence_level"),
            n_studies=r.get("n_studies"),
            pmids=r.get("pmids"),
        )
        for r in results
    ]

    return TaxonDiseasesResponse(
        taxon_id=taxon_id,
        diseases=diseases,
        count=len(diseases),
    )


@router.get("/taxa/{taxon_id}/metabolites", response_model=TaxonMetabolitesResponse)
async def get_taxon_metabolites(
    taxon_id: str,
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    category: Optional[str] = Query(None, description="Filter by metabolite category"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Get metabolites produced by a taxon.

    Returns metabolites that this taxon is known to produce via `PRODUCES` relationships,
    based on curated data from scientific literature.

    **Metabolite categories include:**
    - SCFAs: butyrate, propionate, acetate
    - Amino acid metabolites: GABA, tryptamine, indole
    - Vitamins: B12, folate, K2, riboflavin
    - Bile acids: deoxycholic acid, lithocholic acid
    - Others: TMA, hydrogen sulfide, methane

    **Example:** Faecalibacterium prausnitzii produces butyrate, acetate, and formate.
    """
    kg = get_kg()

    # Normalize taxon_id - handle prefixed IDs
    search_id = taxon_id
    prefixed_id = taxon_id if taxon_id.startswith("NCBITaxon:") else f"NCBITaxon:{taxon_id}"

    # First verify taxon exists
    taxon_check = kg.execute_cypher(
        """
        MATCH (t:Taxon)
        WHERE (t.taxon_id = $prefixed_id OR t.ncbi_tax_id = $search_id OR t.name = $search_id)
          AND (t.organization_id = $organization_id OR t.organization_id IN $public_orgs)
        RETURN t.taxon_id AS id, t.name AS name
        """,
        {"search_id": search_id, "prefixed_id": prefixed_id, **scope_params(scope)},
    )

    if not taxon_check:
        raise HTTPException(status_code=404, detail=f"Taxon '{taxon_id}' not found")

    # Build query with optional category filter
    where_clause = (
        "WHERE (t.taxon_id = $prefixed_id OR t.ncbi_tax_id = $search_id OR t.name = $search_id)"
        "\n      AND (t.organization_id = $organization_id OR t.organization_id IN $public_orgs)"
        "\n      AND (m.organization_id = $organization_id OR m.organization_id IN $public_orgs)"
        "\n      AND " + EDGE_ORG_FILTER("r")  # #297: scope the contributed PRODUCES edge
    )
    if category:
        where_clause += " AND m.category = $category"

    query = f"""
    MATCH (t:Taxon)-[r:PRODUCES]->(m:Compound)
    {where_clause}
    RETURN m.name AS metabolite_name,
           coalesce(m.compound_id, m.metabolite_id) AS metabolite_id,
           m.category AS category,
           collect(DISTINCT r.source) AS sources
    ORDER BY m.name
    LIMIT $limit
    """

    results = kg.execute_cypher(query, {
        "search_id": search_id,
        "prefixed_id": prefixed_id,
        "category": category,
        "limit": limit,
        **scope_params(scope),
    })

    metabolites = [
        TaxonMetabolite(
            metabolite_name=r["metabolite_name"],
            metabolite_id=r.get("metabolite_id"),
            category=r.get("category"),
            sources=[s for s in (r.get("sources") or []) if s] or None,
        )
        for r in results
    ]

    return TaxonMetabolitesResponse(
        taxon_id=taxon_id,
        metabolites=metabolites,
        count=len(metabolites),
    )


@router.get("/taxa/search/{query}")
async def search_taxa(
    query: str,
    limit: int = Query(20, ge=1, le=100, description="Maximum number of results"),
    rank: Optional[str] = Query(None, description="Filter by taxonomic rank"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Search taxa by name.

    Performs case-insensitive partial matching on taxon names.
    Results are sorted by relevance: exact matches first, then prefix matches, then partial matches.

    **Note:** For broader search across all entity types (taxa, diseases, metabolites),
    use `/api/v1/search?q=query` instead.
    """
    kg = get_kg()

    # Build query with optional rank filter
    where_clause = (
        "WHERE t.name =~ $pattern"
        "\n      AND (t.organization_id = $organization_id OR t.organization_id IN $public_orgs)"
    )
    if rank:
        where_clause += " AND t.rank = $rank"

    cypher = f"""
    MATCH (t:Taxon)
    {where_clause}
    WITH t,
         CASE WHEN toLower(t.name) = toLower($query) THEN 0
              WHEN toLower(t.name) STARTS WITH toLower($query) THEN 1
              ELSE 2
         END AS relevance
    RETURN t.ncbi_tax_id AS ncbi_id,
           t.taxon_id AS id,
           t.name AS name,
           t.rank AS rank,
           t.kingdom AS kingdom
    ORDER BY relevance, t.name
    LIMIT $limit
    """

    results = kg.execute_cypher(cypher, {
        "pattern": f"(?i).*{query}.*",
        "query": query,
        "rank": rank,
        "limit": limit,
        **scope_params(scope),
    })

    return {
        "query": query,
        "results": [
            {
                "id": r.get("id"),
                "ncbi_id": r.get("ncbi_id"),
                "name": r["name"],
                "rank": r.get("rank"),
                "kingdom": r.get("kingdom"),
            }
            for r in results
        ],
        "count": len(results),
    }


@router.get("/taxa/{taxon_id}/lineage")
async def get_taxon_lineage(
    taxon_id: str,
    max_depth: int = Query(20, ge=1, le=50, description="Maximum lineage depth"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Get the full taxonomic lineage for a taxon.

    Returns the path from the specified taxon up to the root (domain level),
    following `HAS_PARENT` relationships.

    **Example:** For *Akkermansia muciniphila*, returns:
    - Akkermansia muciniphila (species)
    - Akkermansia (genus)
    - Akkermansiaceae (family)
    - Verrucomicrobiales (order)
    - Verrucomicrobiae (class)
    - Verrucomicrobia (phylum)
    - Bacteria (domain)

    Useful for understanding taxonomic context and building breadcrumb navigation.
    """
    kg = get_kg()

    # Normalize taxon_id
    search_id = taxon_id
    prefixed_id = taxon_id if taxon_id.startswith("NCBITaxon:") else f"NCBITaxon:{taxon_id}"

    # Get the taxon and its lineage using variable-length path
    query = """
    MATCH (t:Taxon)
    WHERE (t.taxon_id = $prefixed_id
       OR t.ncbi_tax_id = $search_id
       OR t.name = $search_id
       OR toLower(t.name) = toLower($search_id))
      AND (t.organization_id = $organization_id OR t.organization_id IN $public_orgs)
    OPTIONAL MATCH path = (t)-[:HAS_PARENT*0..]->(ancestor:Taxon)
    WHERE (NOT (ancestor)-[:HAS_PARENT]->() OR length(path) >= $max_depth)
      AND (ancestor.organization_id = $organization_id OR ancestor.organization_id IN $public_orgs)
    WITH t, ancestor, length(path) as depth
    ORDER BY depth
    WITH t, collect({
        taxon_id: ancestor.taxon_id,
        ncbi_tax_id: ancestor.ncbi_tax_id,
        name: ancestor.name,
        rank: ancestor.rank
    }) as lineage
    RETURN t.taxon_id AS taxon_id,
           t.name AS taxon_name,
           lineage
    """

    results = kg.execute_cypher(query, {
        "search_id": search_id,
        "prefixed_id": prefixed_id,
        "max_depth": max_depth,
        **scope_params(scope),
    })

    if not results:
        raise HTTPException(status_code=404, detail=f"Taxon '{taxon_id}' not found")

    r = results[0]
    lineage = r.get("lineage", [])

    # Filter out None entries and deduplicate
    seen = set()
    unique_lineage = []
    for node in lineage:
        if node.get("name") and node["name"] not in seen:
            seen.add(node["name"])
            unique_lineage.append(node)

    return {
        "taxon_id": taxon_id,
        "taxon_name": r["taxon_name"],
        "lineage": unique_lineage,
        "depth": len(unique_lineage),
    }
