"""
Disease-related API routes.

Provides endpoints for querying disease data, associated taxa,
and relationships between diseases via shared microbiome signatures.
"""

from fastapi import APIRouter, Depends, Query, HTTPException, Request
from typing import Optional

from api.scoping import OrgScope, ORG_FILTER, EDGE_ORG_FILTER, scope_params, resolve_org_scope
from api.models import (
    DiseasesListResponse,
    DiseaseSummary,
    DiseaseDetail,
    DiseaseTaxaResponse,
    DiseaseTaxon,
    RelatedDiseasesResponse,
    RelatedDisease,
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


@router.get("/diseases", response_model=DiseasesListResponse)
async def list_diseases(
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    search: Optional[str] = Query(None, description="Filter by disease name (case-insensitive)"),
    min_taxa: int = Query(0, ge=0, description="Minimum number of associated taxa"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    List all diseases in the knowledge graph.

    Returns diseases with their associated taxa counts, sorted alphabetically.
    Data sources include Disbiome and curated neurological disease associations.

    **Disease categories include:**
    - Gastrointestinal: IBD, Crohn's Disease, Ulcerative Colitis, IBS
    - Metabolic: Type 2 Diabetes, Obesity, NAFLD
    - Neurological: Parkinson's, Alzheimer's, Multiple Sclerosis, Autism
    - Cancer: Colorectal Cancer, Gastric Cancer
    - Autoimmune: Rheumatoid Arthritis, Lupus

    **Examples:**
    - Search for diabetes: `?search=diabetes`
    - Filter diseases with at least 5 associated taxa: `?min_taxa=5`
    """
    kg = get_kg()

    # Build query with optional search filter
    where_clauses = []
    if search:
        where_clauses.append("d.name =~ $search_pattern")
    # Org-scope filter is always present (#200)
    where_clauses.append(ORG_FILTER("d"))

    where_clause = "WHERE " + " AND ".join(where_clauses)

    count_query = f"""
    MATCH (d:Disease)
    {where_clause}
    OPTIONAL MATCH (t:Taxon)-[r:ASSOCIATED_WITH_DISEASE]->(d)
    WHERE {ORG_FILTER("t")} AND {EDGE_ORG_FILTER("r")}
    WITH d, count(DISTINCT t) AS taxa_count
    WHERE taxa_count >= $min_taxa
    RETURN count(d) AS total
    """

    query = f"""
    MATCH (d:Disease)
    {where_clause}
    OPTIONAL MATCH (t:Taxon)-[r:ASSOCIATED_WITH_DISEASE]->(d)
    WHERE {ORG_FILTER("t")} AND {EDGE_ORG_FILTER("r")}
    WITH d, count(DISTINCT t) AS taxa_count, collect(DISTINCT r.source) AS sources
    WHERE taxa_count >= $min_taxa
    RETURN d.name AS name,
           d.name_normalized AS id,
           taxa_count,
           sources
    ORDER BY d.name
    SKIP $offset
    LIMIT $limit
    """

    params = {
        "search_pattern": f"(?i).*{search}.*" if search else None,
        "min_taxa": min_taxa,
        "offset": offset,
        "limit": limit,
    }
    params.update(scope_params(scope))

    # Get total count
    count_result = kg.execute_cypher(count_query, params)
    total = count_result[0]["total"] if count_result else 0

    # Get diseases
    results = kg.execute_cypher(query, params)

    diseases = [
        DiseaseSummary(
            id=r.get("id") or r["name"].lower().replace(" ", "_"),
            name=r["name"],
            taxa_count=r["taxa_count"],
            sources=[s for s in (r.get("sources") or []) if s] or None,
        )
        for r in results
    ]

    return DiseasesListResponse(
        diseases=diseases,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/diseases/compare")
async def compare_diseases(
    ids: str = Query(..., description="Comma-separated disease IDs or names to compare"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Compare microbiome profiles between diseases.

    Returns the taxa associated with each disease, categorized by direction
    (enriched/depleted), plus taxa shared across all diseases.

    **Example:** `/diseases/compare?ids=obesity,type 2 diabetes`

    Useful for:
    - Identifying shared microbial mechanisms across diseases
    - Finding disease-specific biomarkers
    - Understanding comorbidity at the microbiome level
    """
    kg = get_kg()

    disease_list = [d.strip() for d in ids.split(",") if d.strip()]
    if len(disease_list) < 2:
        raise HTTPException(status_code=400, detail="At least 2 diseases required for comparison")
    if len(disease_list) > 5:
        raise HTTPException(status_code=400, detail="Maximum 5 diseases for comparison")

    profiles = []
    all_enriched_sets = []
    all_depleted_sets = []
    all_taxa_by_disease = {}

    for disease_id in disease_list:
        query = """
        MATCH (t:Taxon)-[r:ASSOCIATED_WITH_DISEASE]->(d:Disease)
        WHERE (d.name_normalized = $disease_id
           OR d.name = $disease_id
           OR toLower(d.name) = toLower($disease_id))
          AND (d.organization_id = $organization_id OR d.organization_id IN $public_orgs)
          AND (t.organization_id = $organization_id OR t.organization_id IN $public_orgs)
          AND (r.organization_id IS NULL OR r.organization_id = $organization_id OR r.organization_id IN $public_orgs)
        WITH d, t, r.direction AS direction
        RETURN d.name AS disease_name,
               d.name_normalized AS disease_id,
               collect(CASE WHEN direction IN ['enriched', 'increased'] THEN t.name END) AS enriched,
               collect(CASE WHEN direction IN ['depleted', 'decreased'] THEN t.name END) AS depleted,
               count(DISTINCT t) AS total
        """

        params = {"disease_id": disease_id}
        params.update(scope_params(scope))
        results = kg.execute_cypher(query, params)

        if results:
            r = results[0]
            enriched = [t for t in r.get("enriched", []) if t]
            depleted = [t for t in r.get("depleted", []) if t]

            profiles.append({
                "disease_id": r.get("disease_id") or disease_id,
                "disease_name": r.get("disease_name") or disease_id,
                "enriched_taxa": enriched,
                "depleted_taxa": depleted,
                "total_taxa": r.get("total", 0),
            })
            all_enriched_sets.append(set(enriched))
            all_depleted_sets.append(set(depleted))
            all_taxa_by_disease[r.get("disease_name") or disease_id] = set(enriched + depleted)
        else:
            profiles.append({
                "disease_id": disease_id,
                "disease_name": disease_id,
                "enriched_taxa": [],
                "depleted_taxa": [],
                "total_taxa": 0,
            })
            all_enriched_sets.append(set())
            all_depleted_sets.append(set())
            all_taxa_by_disease[disease_id] = set()

    # Calculate shared taxa
    shared_enriched = set.intersection(*all_enriched_sets) if all_enriched_sets else set()
    shared_depleted = set.intersection(*all_depleted_sets) if all_depleted_sets else set()

    # Calculate unique taxa per disease
    unique_to_each = {}
    for disease_name, taxa in all_taxa_by_disease.items():
        other_taxa = set.union(*[t for n, t in all_taxa_by_disease.items() if n != disease_name]) if len(all_taxa_by_disease) > 1 else set()
        unique_to_each[disease_name] = sorted(taxa - other_taxa)

    return {
        "diseases": profiles,
        "shared_enriched": sorted(shared_enriched),
        "shared_depleted": sorted(shared_depleted),
        "unique_to_each": unique_to_each,
    }


@router.get("/diseases/{disease_id}", response_model=DiseaseDetail)
async def get_disease(
    disease_id: str,
    top_taxa_limit: int = Query(10, ge=1, le=100, description="Number of top taxa to include"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Get detailed information about a specific disease.

    **Accepted disease_id formats:**
    - Normalized name: `type_2_diabetes`, `parkinson's_disease`
    - Exact name: `Type 2 Diabetes`, `Parkinson's Disease`
    - URL-encoded: `parkinson's%20disease`

    Returns disease details including top associated taxa with their direction
    (enriched/depleted).
    """
    kg = get_kg()

    # Query disease by ID or name
    query = """
    MATCH (d:Disease)
    WHERE (d.name_normalized = $disease_id
       OR d.name = $disease_id
       OR toLower(d.name) = toLower($disease_id))
      AND (d.organization_id = $organization_id OR d.organization_id IN $public_orgs)
    OPTIONAL MATCH (t:Taxon)-[r:ASSOCIATED_WITH_DISEASE]->(d)
    WHERE (t.organization_id = $organization_id OR t.organization_id IN $public_orgs)
      AND (r.organization_id IS NULL OR r.organization_id = $organization_id OR r.organization_id IN $public_orgs)
    WITH d, t, r
    ORDER BY t.name
    WITH d,
         count(DISTINCT t) AS taxa_count,
         collect(DISTINCT r.source) AS sources,
         collect({
             name: t.name,
             id: t.ncbi_id,
             rank: t.rank,
             direction: r.direction
         })[0..$top_taxa_limit] AS top_taxa
    RETURN d.name AS name,
           d.name_normalized AS id,
           taxa_count,
           sources,
           top_taxa
    """

    params = {
        "disease_id": disease_id,
        "top_taxa_limit": top_taxa_limit,
    }
    params.update(scope_params(scope))
    results = kg.execute_cypher(query, params)

    if not results:
        raise HTTPException(status_code=404, detail=f"Disease '{disease_id}' not found")

    r = results[0]
    return DiseaseDetail(
        id=r.get("id") or r["name"].lower().replace(" ", "_"),
        name=r["name"],
        taxa_count=r["taxa_count"],
        sources=[s for s in (r.get("sources") or []) if s] or None,
        top_taxa=[t for t in r.get("top_taxa", []) if t.get("name")],
    )


@router.get("/diseases/{disease_id}/taxa")
async def get_disease_taxa(
    disease_id: str,
    request: Request,
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    direction: Optional[str] = Query(
        None,
        description="Filter by direction: 'increased', 'decreased', or 'unchanged'",
    ),
    rank: Optional[str] = Query(
        None,
        description="Filter by taxonomic rank: 'species', 'genus', 'family', etc.",
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
    Get all taxa associated with a disease.

    Returns taxa that have been linked to the disease in scientific literature,
    with their association direction:
    - **enriched/increased**: Taxon abundance is higher in patients
    - **depleted/decreased**: Taxon abundance is lower in patients
    - **altered/unchanged**: Direction varies or is unclear

    **Examples:**
    - Get all taxa for IBD: `/diseases/inflammatory bowel disease/taxa`
    - Get only depleted taxa: `?direction=decreased`
    - Filter by rank: `?rank=genus`
    """
    kg = get_kg()

    # Build WHERE clause for filters
    where_clauses = [
        "(d.name_normalized = $disease_id OR d.name = $disease_id OR toLower(d.name) = toLower($disease_id))"
    ]
    # Org-scope filter (#200) + contributed-edge scope (#297)
    where_clauses.append(ORG_FILTER("d"))
    where_clauses.append(ORG_FILTER("t"))
    where_clauses.append(EDGE_ORG_FILTER("r"))
    if direction:
        where_clauses.append("r.direction = $direction")
    if rank:
        where_clauses.append("t.rank = $rank")
    if evidence_level:
        where_clauses.append("r.evidence_level = $evidence_level")

    where_clause = "WHERE " + " AND ".join(where_clauses)

    count_query = f"""
    MATCH (t:Taxon)-[r:ASSOCIATED_WITH_DISEASE]->(d:Disease)
    {where_clause}
    RETURN count(t) AS total
    """

    # Determine sort order
    sort_clause = "ORDER BY t.name"
    if sort_by == "effect_size":
        sort_clause = "ORDER BY r.effect_size DESC"
    elif sort_by == "p_value":
        sort_clause = "ORDER BY r.p_value ASC"
    elif sort_by == "n_studies":
        sort_clause = "ORDER BY r.n_studies DESC"

    query = f"""
    MATCH (t:Taxon)-[r:ASSOCIATED_WITH_DISEASE]->(d:Disease)
    {where_clause}
    RETURN t.name AS taxon_name,
           t.ncbi_tax_id AS ncbi_id,
           t.taxon_id AS taxon_id,
           t.rank AS rank,
           r.direction AS direction,
           r.qualitative_outcome AS outcome,
           r.effect_size AS effect_size,
           r.p_value AS p_value,
           r.evidence_level AS evidence_level,
           r.n_studies AS n_studies,
           r.pmids AS pmids,
           collect(DISTINCT r.source) AS sources
    {sort_clause}
    SKIP $offset
    LIMIT $limit
    """

    params = {
        "disease_id": disease_id,
        "direction": direction,
        "rank": rank,
        "evidence_level": evidence_level,
        "offset": offset,
        "limit": limit,
    }
    params.update(scope_params(scope))

    # Check if disease exists first
    disease_check = kg.execute_cypher(
        """
        MATCH (d:Disease)
        WHERE (d.name_normalized = $disease_id
           OR d.name = $disease_id
           OR toLower(d.name) = toLower($disease_id))
          AND (d.organization_id = $organization_id OR d.organization_id IN $public_orgs)
        RETURN d.name AS name
        """,
        {"disease_id": disease_id, **scope_params(scope)},
    )

    if not disease_check:
        raise HTTPException(status_code=404, detail=f"Disease '{disease_id}' not found")

    count_result = kg.execute_cypher(count_query, params)
    total = count_result[0]["total"] if count_result else 0

    results = kg.execute_cypher(query, params)

    taxa = [
        DiseaseTaxon(
            taxon_name=r["taxon_name"],
            taxon_id=r.get("taxon_id"),
            ncbi_id=r.get("ncbi_id"),
            rank=r.get("rank"),
            direction=r.get("direction"),
            outcome=r.get("outcome"),
            effect_size=r.get("effect_size"),
            p_value=r.get("p_value"),
            evidence_level=r.get("evidence_level"),
            n_studies=r.get("n_studies"),
            pmids=r.get("pmids"),
            sources=[s for s in (r.get("sources") or []) if s] or None,
        )
        for r in results
    ]

    return DiseaseTaxaResponse(
        disease=disease_id,
        taxa=taxa,
        count=total,
    )


@router.get("/diseases/{disease_id}/related", response_model=RelatedDiseasesResponse)
async def get_related_diseases(
    disease_id: str,
    limit: int = Query(20, ge=1, le=100, description="Maximum number of results"),
    min_shared_taxa: int = Query(1, ge=1, description="Minimum shared taxa for relatedness"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Get diseases with similar microbiome signatures.

    Two diseases are considered related if they share associated taxa.
    The more taxa they share, the stronger the microbiome similarity.

    This can help identify:
    - Diseases with shared microbial mechanisms
    - Potential comorbidity patterns
    - Common therapeutic targets

    **Example:** Obesity and Type 2 Diabetes share multiple associated taxa,
    reflecting their metabolic relationship.
    """
    kg = get_kg()

    # First verify the disease exists
    disease_check = kg.execute_cypher(
        """
        MATCH (d:Disease)
        WHERE (d.name_normalized = $disease_id
           OR d.name = $disease_id
           OR toLower(d.name) = toLower($disease_id))
          AND (d.organization_id = $organization_id OR d.organization_id IN $public_orgs)
        RETURN d.name AS name, d.name_normalized AS id
        """,
        {"disease_id": disease_id, **scope_params(scope)},
    )

    if not disease_check:
        raise HTTPException(status_code=404, detail=f"Disease '{disease_id}' not found")

    # Find related diseases based on shared taxa
    query = """
    MATCH (d1:Disease)<-[:ASSOCIATED_WITH_DISEASE]-(t:Taxon)-[:ASSOCIATED_WITH_DISEASE]->(d2:Disease)
    WHERE (d1.name_normalized = $disease_id
           OR d1.name = $disease_id
           OR toLower(d1.name) = toLower($disease_id))
      AND (d1.organization_id = $organization_id OR d1.organization_id IN $public_orgs)
      AND (d2.organization_id = $organization_id OR d2.organization_id IN $public_orgs)
      AND (t.organization_id = $organization_id OR t.organization_id IN $public_orgs)
      AND d1 <> d2
    WITH d2, count(DISTINCT t) AS shared_taxa
    WHERE shared_taxa >= $min_shared_taxa
    RETURN d2.name AS disease_name,
           d2.name_normalized AS disease_id,
           shared_taxa
    ORDER BY shared_taxa DESC, d2.name
    LIMIT $limit
    """

    params = {
        "disease_id": disease_id,
        "min_shared_taxa": min_shared_taxa,
        "limit": limit,
    }
    params.update(scope_params(scope))
    results = kg.execute_cypher(query, params)

    related = [
        RelatedDisease(
            disease_name=r["disease_name"],
            disease_id=r.get("disease_id") or r["disease_name"].lower().replace(" ", "_"),
            shared_taxa=r["shared_taxa"],
        )
        for r in results
    ]

    return RelatedDiseasesResponse(
        disease_id=disease_id,
        related=related,
        count=len(related),
    )


@router.get("/diseases/{disease_id}/metabolites")
async def get_disease_metabolites(
    disease_id: str,
    request: Request,
    limit: int = Query(100, ge=1, le=500, description="Maximum number of results"),
    min_producers: int = Query(1, ge=1, description="Minimum number of producing taxa"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Get metabolites associated with a disease via producing taxa.

    Finds all metabolites produced by taxa that are associated with the disease.
    This reveals the metabolic signature of a disease's microbiome.

    **Use cases:**
    - Identify metabolites that may be altered in a disease
    - Find potential biomarkers (metabolites from enriched/depleted taxa)
    - Understand metabolic pathways affected by dysbiosis

    **Example:** For IBD, returns butyrate (depleted producers like Faecalibacterium),
    indicating reduced SCFA production in the disease state.
    """
    kg = get_kg()

    # First verify disease exists
    disease_check = kg.execute_cypher(
        """
        MATCH (d:Disease)
        WHERE (d.name_normalized = $disease_id
           OR d.name = $disease_id
           OR toLower(d.name) = toLower($disease_id))
          AND (d.organization_id = $organization_id OR d.organization_id IN $public_orgs)
        RETURN d.name AS name
        """,
        {"disease_id": disease_id, **scope_params(scope)},
    )

    if not disease_check:
        raise HTTPException(status_code=404, detail=f"Disease '{disease_id}' not found")

    # Get metabolites via disease-associated taxa
    query = """
    MATCH (t:Taxon)-[r:ASSOCIATED_WITH_DISEASE]->(d:Disease)
    WHERE (d.name_normalized = $disease_id
       OR d.name = $disease_id
       OR toLower(d.name) = toLower($disease_id))
      AND (d.organization_id = $organization_id OR d.organization_id IN $public_orgs)
      AND (t.organization_id = $organization_id OR t.organization_id IN $public_orgs)
      AND (r.organization_id IS NULL OR r.organization_id = $organization_id OR r.organization_id IN $public_orgs)
    MATCH (t)-[rp:PRODUCES]->(m:Compound)
    WHERE (m.organization_id = $organization_id OR m.organization_id IN $public_orgs)
      AND (rp.organization_id IS NULL OR rp.organization_id = $organization_id OR rp.organization_id IN $public_orgs)
    WITH m, collect(DISTINCT {name: t.name, direction: r.direction}) AS producers
    WHERE size(producers) >= $min_producers
    RETURN m.name AS metabolite_name,
           coalesce(m.compound_id, m.metabolite_id) AS metabolite_id,
           size(producers) AS producer_count,
           [p IN producers | p.name] AS producer_names,
           [p IN producers | p.direction] AS directions
    ORDER BY producer_count DESC, m.name
    LIMIT $limit
    """

    params = {
        "disease_id": disease_id,
        "min_producers": min_producers,
        "limit": limit,
    }
    params.update(scope_params(scope))
    results = kg.execute_cypher(query, params)

    metabolites = []
    for r in results:
        directions = [d for d in r.get("directions", []) if d]
        enriched = directions.count("enriched") + directions.count("increased")
        depleted = directions.count("depleted") + directions.count("decreased")

        if enriched > depleted:
            predominant = "enriched"
        elif depleted > enriched:
            predominant = "depleted"
        else:
            predominant = "mixed"

        metabolites.append({
            "metabolite_name": r["metabolite_name"],
            "metabolite_id": r.get("metabolite_id"),
            "producer_count": r["producer_count"],
            "producers": r.get("producer_names", []),
            "direction": predominant,
        })

    return {
        "disease": disease_id,
        "metabolites": metabolites,
        "count": len(metabolites),
    }
