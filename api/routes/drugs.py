"""
Drug-related API routes.

Provides endpoints for querying drug data, drug-taxa interactions,
and drug-disease connections via the microbiome.
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


@router.get("/drugs")
async def list_drugs(
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    search: Optional[str] = Query(None, description="Filter by drug name (case-insensitive)"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    List all drugs in the knowledge graph.

    Returns a paginated list of drugs. Use the `search` parameter
    to filter by name (case-insensitive partial match).

    **Examples:**
    - List first 100 drugs: `/drugs`
    - Search for metformin: `/drugs?search=metformin`
    - Paginate: `/drugs?limit=50&offset=100`
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
    MATCH (d:Drug)
    {where_clause}
    RETURN count(d) AS total
    """

    query = f"""
    MATCH (d:Drug)
    {where_clause}
    RETURN d.name AS name,
           d.drugbank_id AS drugbank_id,
           d.drug_id AS drug_id,
           d.description AS description,
           d.category AS category
    ORDER BY d.name
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

    drugs = [
        {
            "name": r["name"],
            "drugbank_id": r.get("drugbank_id"),
            "drug_id": r.get("drug_id"),
            "description": r.get("description"),
            "category": r.get("category"),
        }
        for r in results
    ]

    return {
        "drugs": drugs,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/drugs/search/{query}")
async def search_drugs(
    query: str,
    limit: int = Query(20, ge=1, le=100, description="Maximum number of results"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Search drugs by name.

    Performs case-insensitive partial matching on drug names.
    Results are sorted by relevance: exact matches first, then prefix matches,
    then partial matches.

    **Examples:**
    - Search for aspirin: `/drugs/search/aspirin`
    - Search for statins: `/drugs/search/statin`
    """
    kg = get_kg()

    cypher = """
    MATCH (d:Drug)
    WHERE d.name =~ $pattern
      AND (d.organization_id = $organization_id OR d.organization_id IN $public_orgs)
    WITH d,
         CASE WHEN toLower(d.name) = toLower($query) THEN 0
              WHEN toLower(d.name) STARTS WITH toLower($query) THEN 1
              ELSE 2
         END AS relevance
    RETURN d.name AS name,
           d.drugbank_id AS drugbank_id,
           d.drug_id AS drug_id,
           d.description AS description,
           d.category AS category
    ORDER BY relevance, d.name
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
                "name": r["name"],
                "drugbank_id": r.get("drugbank_id"),
                "drug_id": r.get("drug_id"),
                "description": r.get("description"),
                "category": r.get("category"),
            }
            for r in results
        ],
        "count": len(results),
    }


@router.get("/drugs/{drug_id}")
async def get_drug(
    drug_id: str,
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Get detailed information about a specific drug.

    **Accepted drug_id formats:**
    - DrugBank ID: `DB00945`
    - Drug name: `Aspirin`
    - Internal drug_id

    Returns drug details including counts of related targets and diseases.
    """
    kg = get_kg()

    # Normalize drug_id - handle multiple formats
    search_id = drug_id

    query = """
    MATCH (d:Drug)
    WHERE (d.drug_id = $id
       OR d.drugbank_id = $id
       OR toLower(d.name) = toLower($id))
      AND (d.organization_id = $organization_id OR d.organization_id IN $public_orgs)
    OPTIONAL MATCH (d)-[rt:EFFECTIVE_AGAINST]->(t:Taxon)
    WHERE (t.organization_id = $organization_id OR t.organization_id IN $public_orgs)
    WITH d, count(DISTINCT t) AS target_count
    OPTIONAL MATCH (d2:Drug)-[:EFFECTIVE_AGAINST]->(:Taxon)-[:ASSOCIATED_WITH_DISEASE]->(dis:Disease)
    WHERE d2 = d
      AND (dis.organization_id = $organization_id OR dis.organization_id IN $public_orgs)
    WITH d, target_count, count(DISTINCT dis) AS disease_count
    RETURN d AS drug_node,
           target_count,
           disease_count
    """

    params = {"id": search_id}
    params.update(scope_params(scope))
    results = kg.execute_cypher(query, params)

    if not results:
        raise HTTPException(status_code=404, detail=f"Drug '{drug_id}' not found")

    r = results[0]
    drug_node = r.get("drug_node", {})

    # Build response from all drug node properties
    drug = dict(drug_node) if drug_node else {}
    drug["target_count"] = r.get("target_count", 0)
    drug["disease_count"] = r.get("disease_count", 0)

    return drug


@router.get("/drugs/{drug_id}/taxa")
async def get_drug_taxa(
    drug_id: str,
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Get taxa that metabolize or interact with a drug.

    Returns taxa connected to the drug via any relationship type, including
    relationship properties such as mechanism. In practice the graph holds
    exactly one Drug->Taxon edge type, `EFFECTIVE_AGAINST` (294,275 edges);
    the untyped match here is why this endpoint kept working while the drug
    detail and disease endpoints, which named a non-existent `AFFECTS_TAXON`,
    silently reported zero (#305).

    **Examples:**
    - Get taxa for metformin: `/drugs/DB00331/taxa`
    """
    kg = get_kg()

    search_id = drug_id

    # Verify drug exists
    drug_check = kg.execute_cypher(
        """
        MATCH (d:Drug)
        WHERE (d.drug_id = $id
           OR d.drugbank_id = $id
           OR toLower(d.name) = toLower($id))
          AND (d.organization_id = $organization_id OR d.organization_id IN $public_orgs)
        RETURN d.name AS name
        """,
        {"id": search_id, **scope_params(scope)},
    )

    if not drug_check:
        raise HTTPException(status_code=404, detail=f"Drug '{drug_id}' not found")

    # Get taxa connected to the drug in either direction
    query = """
    MATCH (d:Drug)
    WHERE (d.drug_id = $id
       OR d.drugbank_id = $id
       OR toLower(d.name) = toLower($id))
      AND (d.organization_id = $organization_id OR d.organization_id IN $public_orgs)
    MATCH (d)-[r]-(t:Taxon)
    WHERE (t.organization_id = $organization_id OR t.organization_id IN $public_orgs)
    RETURN t.name AS taxon_name,
           t.taxon_id AS taxon_id,
           t.ncbi_tax_id AS ncbi_tax_id,
           t.rank AS rank,
           type(r) AS relationship_type,
           properties(r) AS relationship_properties
    ORDER BY t.name
    LIMIT $limit
    """

    params = {
        "id": search_id,
        "limit": limit,
    }
    params.update(scope_params(scope))
    results = kg.execute_cypher(query, params)

    taxa = [
        {
            "taxon_name": r["taxon_name"],
            "taxon_id": r.get("taxon_id"),
            "ncbi_tax_id": r.get("ncbi_tax_id"),
            "rank": r.get("rank"),
            "relationship_type": r.get("relationship_type"),
            "relationship_properties": r.get("relationship_properties", {}),
        }
        for r in results
    ]

    return {
        "drug_id": drug_id,
        "taxa": taxa,
        "count": len(taxa),
    }


@router.get("/drugs/{drug_id}/diseases")
async def get_drug_diseases(
    drug_id: str,
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Get disease connections for a drug via the microbiome.

    Finds diseases connected to the drug through taxa that the drug
    affects and that are associated with diseases. This reveals potential
    drug-disease relationships mediated by the gut microbiome.

    **Examples:**
    - Get disease connections for metformin: `/drugs/DB00331/diseases`
    """
    kg = get_kg()

    search_id = drug_id

    # Verify drug exists
    drug_check = kg.execute_cypher(
        """
        MATCH (d:Drug)
        WHERE (d.drug_id = $id
           OR d.drugbank_id = $id
           OR toLower(d.name) = toLower($id))
          AND (d.organization_id = $organization_id OR d.organization_id IN $public_orgs)
        RETURN d.name AS name
        """,
        {"id": search_id, **scope_params(scope)},
    )

    if not drug_check:
        raise HTTPException(status_code=404, detail=f"Drug '{drug_id}' not found")

    # Find diseases connected via drug -> taxon -> disease path
    query = """
    MATCH (d:Drug)
    WHERE (d.drug_id = $id
       OR d.drugbank_id = $id
       OR toLower(d.name) = toLower($id))
      AND (d.organization_id = $organization_id OR d.organization_id IN $public_orgs)
    MATCH (d)-[r1:EFFECTIVE_AGAINST]->(t:Taxon)-[r2:ASSOCIATED_WITH_DISEASE]->(dis:Disease)
    WHERE (t.organization_id = $organization_id OR t.organization_id IN $public_orgs)
      AND (dis.organization_id = $organization_id OR dis.organization_id IN $public_orgs)
    RETURN dis.name AS disease_name,
           dis.name_normalized AS disease_id,
           collect(DISTINCT {
               taxon_name: t.name,
               taxon_id: t.taxon_id,
               drug_effect: r1.mechanism,
               disease_direction: r2.direction
           }) AS mediating_taxa,
           count(DISTINCT t) AS mediating_taxa_count
    ORDER BY mediating_taxa_count DESC, dis.name
    LIMIT $limit
    """

    params = {
        "id": search_id,
        "limit": limit,
    }
    params.update(scope_params(scope))
    results = kg.execute_cypher(query, params)

    diseases = [
        {
            "disease_name": r["disease_name"],
            "disease_id": r.get("disease_id"),
            "mediating_taxa": r.get("mediating_taxa", []),
            "mediating_taxa_count": r.get("mediating_taxa_count", 0),
        }
        for r in results
    ]

    return {
        "drug_id": drug_id,
        "diseases": diseases,
        "count": len(diseases),
    }
