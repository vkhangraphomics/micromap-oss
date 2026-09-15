"""
Discovery API routes for probiotic candidates and SCFA producers.

Provides endpoints for identifying potential probiotic candidates for diseases
and querying short-chain fatty acid (SCFA) producing taxa.
"""

from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Optional

from api.scoping import OrgScope, ORG_FILTER, EDGE_ORG_FILTER, scope_params, resolve_org_scope
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


@router.get("/discovery/probiotics/{disease_id}")
async def find_probiotic_candidates(
    disease_id: str,
    limit: int = Query(20, ge=1, le=100, description="Maximum number of results"),
    min_studies: Optional[int] = Query(None, ge=1, description="Minimum number of supporting studies"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Find probiotic candidates for a specific disease.

    Identifies taxa that are depleted in the disease, are non-pathogenic,
    and preferably produce short-chain fatty acids (SCFAs). Taxa with
    probiotic_potential flagged are prioritized.

    **Logic:**
    - Find taxa depleted in this disease (direction = 'depleted')
    - Filter to non-pathogenic taxa (is_pathogen IS NULL OR is_pathogen = false)
    - Prefer taxa that produce SCFAs
    - Include probiotic_potential property if available

    **Accepted disease_id formats:**
    - Normalized name: `type_2_diabetes`, `inflammatory_bowel_disease`
    - Exact name: `Type 2 Diabetes`, `Inflammatory Bowel Disease`
    - URL-encoded: `parkinson's%20disease`

    **Examples:**
    - Find probiotics for IBD: `/discovery/probiotics/inflammatory bowel disease`
    - With minimum study threshold: `?min_studies=3`
    """
    kg = get_kg()

    # Verify disease exists and get its canonical name
    disease_check = kg.execute_cypher(
        f"""
        MATCH (d:Disease)
        WHERE (d.name_normalized = $disease_id
           OR d.name = $disease_id
           OR toLower(d.name) = toLower($disease_id)
           OR d.disease_id = $disease_id)
          AND {ORG_FILTER("d")}
        RETURN d.name AS name, d.name_normalized AS id, d.disease_id AS disease_id
        """,
        {"disease_id": disease_id, **scope_params(scope)},
    )

    if not disease_check:
        raise HTTPException(status_code=404, detail=f"Disease '{disease_id}' not found")

    disease_info = disease_check[0]
    disease_name = disease_info["name"]
    canonical_id = disease_info.get("id") or disease_info.get("disease_id") or disease_id

    # Build optional min_studies filter
    studies_filter = ""
    if min_studies is not None:
        studies_filter = "AND r.n_studies >= $min_studies"

    query = f"""
    MATCH (t:Taxon)-[r:ASSOCIATED_WITH_DISEASE]->(d:Disease)
    WHERE (d.name_normalized = $disease_id
           OR d.name = $disease_id
           OR toLower(d.name) = toLower($disease_id)
           OR d.disease_id = $disease_id)
      AND r.direction = 'depleted'
      AND (t.is_pathogen IS NULL OR t.is_pathogen = false)
      AND {ORG_FILTER("t")}
      AND {ORG_FILTER("d")}
      AND {EDGE_ORG_FILTER("r")}
      {studies_filter}
    OPTIONAL MATCH (t)-[:PRODUCES]->(m:Compound)
    WHERE m.is_scfa = true
      AND {ORG_FILTER("m")}
    RETURN t.name AS taxon_name,
           t.taxon_id AS taxon_id,
           t.ncbi_tax_id AS ncbi_id,
           t.rank AS rank,
           t.genus AS genus,
           t.probiotic_potential AS probiotic_potential,
           r.direction AS direction,
           r.n_studies AS n_studies,
           r.source AS source,
           collect(DISTINCT m.name) AS scfa_names
    ORDER BY t.probiotic_potential DESC, size(collect(DISTINCT m.name)) DESC, t.name
    LIMIT $limit
    """

    params = {
        "disease_id": disease_id,
        "min_studies": min_studies,
        "limit": limit,
    }
    params.update(scope_params(scope))

    results = kg.execute_cypher(query, params)

    candidates = []
    for r in results:
        scfa_list = [s for s in (r.get("scfa_names") or []) if s]
        candidates.append({
            "taxon_name": r["taxon_name"],
            "taxon_id": r.get("taxon_id"),
            "ncbi_id": r.get("ncbi_id"),
            "rank": r.get("rank"),
            "genus": r.get("genus"),
            "probiotic_potential": r.get("probiotic_potential"),
            "scfa_production": scfa_list,
            "evidence": {
                "direction": r.get("direction"),
                "n_studies": r.get("n_studies"),
                "sources": [r["source"]] if r.get("source") else [],
            },
        })

    return {
        "disease_id": canonical_id,
        "disease_name": disease_name,
        "candidates": candidates,
        "count": len(candidates),
    }


@router.get("/discovery/scfa-producers")
async def get_scfa_producers(
    limit: int = Query(50, ge=1, le=100, description="Maximum number of results"),
    metabolite: Optional[str] = Query(None, description="Filter to a specific SCFA (e.g., 'butyrate')"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Get SCFA-producing taxa from the knowledge graph.

    Returns taxa that produce short-chain fatty acids (SCFAs), grouped by
    taxon with their produced metabolites. Includes carbon chain length
    information when available.

    **SCFAs covered:**
    - Acetate (C2)
    - Propionate (C3)
    - Butyrate (C4)
    - Other short-chain fatty acids

    **Examples:**
    - Get all SCFA producers: `/discovery/scfa-producers`
    - Filter to butyrate producers: `?metabolite=butyrate`
    - Limit results: `?limit=10`
    """
    kg = get_kg()

    # Build optional metabolite filter
    metabolite_filter = ""
    if metabolite:
        metabolite_filter = "AND toLower(m.name) = toLower($metabolite)"

    query = f"""
    MATCH (t:Taxon)-[:PRODUCES]->(m:Compound)
    WHERE m.is_scfa = true
      AND {ORG_FILTER("t")}
      AND {ORG_FILTER("m")}
      {metabolite_filter}
    WITH t, collect(DISTINCT {{
        name: m.name,
        metabolite_id: coalesce(m.compound_id, m.metabolite_id),
        carbon_chain_length: m.carbon_chain_length
    }}) AS metabolites
    RETURN t.name AS taxon_name,
           t.taxon_id AS taxon_id,
           t.ncbi_tax_id AS ncbi_id,
           t.rank AS rank,
           metabolites,
           size(metabolites) AS metabolite_count
    ORDER BY metabolite_count DESC, t.name
    LIMIT $limit
    """

    params = {
        "metabolite": metabolite,
        "limit": limit,
    }
    params.update(scope_params(scope))

    results = kg.execute_cypher(query, params)

    producers = []
    for r in results:
        scfa_metabolites = []
        for m in (r.get("metabolites") or []):
            if m and m.get("name"):
                scfa_metabolites.append({
                    "name": m["name"],
                    "metabolite_id": m.get("metabolite_id"),
                    "carbon_chain_length": m.get("carbon_chain_length"),
                })
        producers.append({
            "taxon_name": r["taxon_name"],
            "taxon_id": r.get("taxon_id"),
            "ncbi_id": r.get("ncbi_id"),
            "rank": r.get("rank"),
            "scfa_metabolites": scfa_metabolites,
            "metabolite_count": r.get("metabolite_count", 0),
        })

    return {
        "producers": producers,
        "count": len(producers),
    }
