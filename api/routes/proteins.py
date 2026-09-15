"""
Protein-related API routes.

Provides endpoints for querying protein data and their drug targets.
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


@router.get("/proteins")
async def list_proteins(
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    search: Optional[str] = Query(None, description="Search by protein name (case-insensitive partial match)"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    List proteins with optional filtering.

    Returns a paginated list of proteins in the knowledge graph.

    **Examples:**
    - List all proteins: `/proteins`
    - Search for a protein: `/proteins?search=kinase`
    - Paginate: `/proteins?limit=50&offset=100`
    """
    kg = get_kg()

    # Build WHERE clauses
    where_clauses = []
    if search:
        where_clauses.append("p.name =~ $search_pattern")
    # Org-scope filter is always present (#200)
    where_clauses.append(ORG_FILTER("p"))

    where_clause = "WHERE " + " AND ".join(where_clauses)

    count_query = f"""
    MATCH (p:Protein)
    {where_clause}
    RETURN count(p) AS total
    """

    query = f"""
    MATCH (p:Protein)
    {where_clause}
    RETURN p.name AS name,
           p.protein_id AS protein_id,
           p.uniprot_id AS uniprot_id,
           p.description AS description
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

    proteins = [
        {
            "protein_id": r.get("protein_id"),
            "uniprot_id": r.get("uniprot_id"),
            "name": r.get("name"),
            "description": r.get("description"),
        }
        for r in results
    ]

    return {
        "proteins": proteins,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/proteins/{protein_id}")
async def get_protein(
    protein_id: str,
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Get detailed information about a specific protein.

    **Accepted protein_id formats:**
    - Protein ID: `12345`
    - UniProt ID: `P12345`
    - Protein name: `Albumin`

    Returns protein details including the count of drugs targeting this protein.
    """
    kg = get_kg()

    # Normalize protein_id
    search_id = protein_id

    query = """
    MATCH (p:Protein)
    WHERE (p.protein_id = $search_id
       OR p.uniprot_id = $search_id
       OR p.name = $search_id
       OR toLower(p.name) = toLower($search_id))
      AND (p.organization_id = $organization_id OR p.organization_id IN $public_orgs)
    OPTIONAL MATCH (d:Drug)-[:TARGETS]->(p)
    WHERE (d.organization_id = $organization_id OR d.organization_id IN $public_orgs)
    WITH p, count(DISTINCT d) AS drug_targets_count
    RETURN p.protein_id AS protein_id,
           p.uniprot_id AS uniprot_id,
           p.name AS name,
           p.description AS description,
           drug_targets_count
    LIMIT 1
    """

    params = {"search_id": search_id}
    params.update(scope_params(scope))
    results = kg.execute_cypher(query, params)

    if not results:
        raise HTTPException(status_code=404, detail=f"Protein '{protein_id}' not found")

    r = results[0]
    return {
        "protein_id": r.get("protein_id"),
        "uniprot_id": r.get("uniprot_id"),
        "name": r.get("name"),
        "description": r.get("description"),
        "drug_targets_count": r.get("drug_targets_count", 0),
    }


@router.get("/proteins/{protein_id}/drugs")
async def get_protein_drugs(
    protein_id: str,
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Get drugs that target this protein.

    Returns drugs linked to the specified protein via `TARGETS` relationships.

    **Example:** `/proteins/P12345/drugs` returns drugs known to target
    the protein with UniProt ID P12345.
    """
    kg = get_kg()

    # Normalize protein_id
    search_id = protein_id

    # First verify protein exists
    protein_check = kg.execute_cypher(
        """
        MATCH (p:Protein)
        WHERE (p.protein_id = $search_id
           OR p.uniprot_id = $search_id
           OR p.name = $search_id
           OR toLower(p.name) = toLower($search_id))
          AND (p.organization_id = $organization_id OR p.organization_id IN $public_orgs)
        RETURN p.protein_id AS protein_id, p.name AS name
        LIMIT 1
        """,
        {"search_id": search_id, **scope_params(scope)},
    )

    if not protein_check:
        raise HTTPException(status_code=404, detail=f"Protein '{protein_id}' not found")

    query = """
    MATCH (d:Drug)-[:TARGETS]->(p:Protein)
    WHERE (p.protein_id = $search_id
       OR p.uniprot_id = $search_id
       OR p.name = $search_id
       OR toLower(p.name) = toLower($search_id))
      AND (p.organization_id = $organization_id OR p.organization_id IN $public_orgs)
      AND (d.organization_id = $organization_id OR d.organization_id IN $public_orgs)
    RETURN d.name AS drug_name,
           d.drug_id AS drug_id,
           d.drugbank_id AS drugbank_id,
           d.description AS description
    ORDER BY d.name
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

    drugs = [
        {
            "drug_name": r.get("drug_name"),
            "drug_id": r.get("drug_id"),
            "drugbank_id": r.get("drugbank_id"),
            "description": r.get("description"),
        }
        for r in results
    ]

    return {
        "protein_id": protein_id,
        "drugs": drugs,
        "count": len(drugs),
    }
