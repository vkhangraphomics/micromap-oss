"""
Search API routes.

Provides full-text search and autocomplete functionality
across all entity types in the knowledge graph.
"""

from fastapi import APIRouter, Depends, Query, Request
from typing import Optional

from api.scoping import OrgScope, ORG_FILTER, scope_params, resolve_org_scope
from api.models import (
    SearchResponse,
    SearchResult,
    SuggestionsResponse,
    SearchSuggestion,
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


@router.get("/search")
async def full_text_search(
    request: Request,
    q: str = Query(..., min_length=2, description="Search query (minimum 2 characters)"),
    types: Optional[str] = Query(
        None,
        description="Comma-separated entity types to search: 'Taxon', 'Disease', 'Metabolite'. Leave empty for all.",
    ),
    limit: int = Query(50, ge=1, le=500, description="Maximum total results"),
    source: Optional[str] = Query(
        None,
        description="Filter results by data source (e.g., 'disbiome', 'ncbi'). Only returns entities from the specified source.",
    ),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Full-text search across the knowledge graph.

    Searches across taxa (by scientific and common name), diseases, and metabolites.
    Results are sorted by relevance: exact matches first, then prefix matches, then partial matches.

    **Example queries:**
    - `Lactobacillus` - Find all Lactobacillus species and strains
    - `diabetes` - Find Type 2 Diabetes, related taxa
    - `butyrate` - Find butyrate metabolite
    - `parkinson` - Find Parkinson's Disease and associated research

    **Filter by type:**
    - `?q=lacto&types=Taxon` - Only search taxa
    - `?q=diabetes&types=Disease` - Only search diseases
    - `?q=butyrate&types=Metabolite` - Only search metabolites
    """
    kg = get_kg()

    type_list = [t.strip() for t in types.split(",")] if types else None
    results = []

    # Calculate per-type limit to balance results
    if type_list:
        per_type_limit = limit
    else:
        per_type_limit = limit // 3 + 10  # Extra to allow sorting

    # Use toLower + CONTAINS for case-insensitive matching
    # Much faster than regex =~ on large node sets
    q_lower = q.lower()

    source_filter_taxon = "AND $source IN t.sources" if source else ""
    source_filter_disease = "AND $source IN d.sources" if source else ""
    source_filter_metabolite = "AND $source IN m.sources" if source else ""

    # Search Taxa
    if not type_list or "Taxon" in type_list:
        taxa_query = f"""
        MATCH (t:Taxon)
        WHERE (toLower(t.name) CONTAINS $q_lower
           OR toLower(t.common_name) CONTAINS $q_lower)
          AND {ORG_FILTER("t")}
        {source_filter_taxon}
        WITH t,
             CASE
                 WHEN toLower(t.name) = $q_lower THEN 0
                 WHEN toLower(t.name) STARTS WITH $q_lower THEN 1
                 ELSE 2
             END AS relevance
        RETURN 'Taxon' AS type,
               t.taxon_id AS id,
               t.name AS name,
               t.rank AS rank,
               NULL AS category,
               relevance
        ORDER BY relevance, t.name
        LIMIT $limit
        """

        taxa_params = {"q_lower": q_lower, "limit": per_type_limit}
        if source:
            taxa_params["source"] = source
        taxa_params.update(scope_params(scope))
        taxa_results = kg.execute_cypher(taxa_query, taxa_params)
        results.extend(taxa_results)

    # Search Diseases
    if not type_list or "Disease" in type_list:
        disease_query = f"""
        MATCH (d:Disease)
        WHERE toLower(d.name) CONTAINS $q_lower
          AND {ORG_FILTER("d")}
        {source_filter_disease}
        WITH d,
             CASE
                 WHEN toLower(d.name) = $q_lower THEN 0
                 WHEN toLower(d.name) STARTS WITH $q_lower THEN 1
                 ELSE 2
             END AS relevance
        RETURN 'Disease' AS type,
               coalesce(d.name_normalized, toLower(replace(d.name, ' ', '_'))) AS id,
               d.name AS name,
               NULL AS rank,
               NULL AS category,
               relevance
        ORDER BY relevance, d.name
        LIMIT $limit
        """

        disease_params = {"q_lower": q_lower, "limit": per_type_limit}
        if source:
            disease_params["source"] = source
        disease_params.update(scope_params(scope))
        disease_results = kg.execute_cypher(disease_query, disease_params)
        results.extend(disease_results)

    # Search Metabolites
    if not type_list or "Metabolite" in type_list:
        metabolite_query = f"""
        MATCH (m:Compound)
        WHERE toLower(m.name) CONTAINS $q_lower
          AND {ORG_FILTER("m")}
        {source_filter_metabolite}
        WITH m,
             CASE
                 WHEN toLower(m.name) = $q_lower THEN 0
                 WHEN toLower(m.name) STARTS WITH $q_lower THEN 1
                 ELSE 2
             END AS relevance
        RETURN 'Metabolite' AS type,
               coalesce(m.compound_id, m.metabolite_id, m.hmdb_id, m.kegg_id) AS id,
               m.name AS name,
               NULL AS rank,
               m.category AS category,
               relevance
        ORDER BY relevance, m.name
        LIMIT $limit
        """

        metabolite_params = {"q_lower": q_lower, "limit": per_type_limit}
        if source:
            metabolite_params["source"] = source
        metabolite_params.update(scope_params(scope))
        metabolite_results = kg.execute_cypher(metabolite_query, metabolite_params)
        results.extend(metabolite_results)

    # Sort combined results by relevance and limit
    results.sort(key=lambda x: (x.get("relevance", 2), x.get("name", "")))
    results = results[:limit]

    search_results = [
        SearchResult(
            type=r["type"],
            id=r.get("id") or "",
            name=r["name"],
            rank=r.get("rank"),
            category=r.get("category"),
        )
        for r in results
    ]

    return SearchResponse(
        query=q,
        types=type_list,
        results=search_results,
        count=len(search_results),
    )


@router.get("/search/suggest", response_model=SuggestionsResponse)
async def search_suggestions(
    q: str = Query(..., min_length=2, description="Query prefix for autocomplete"),
    limit: int = Query(10, ge=1, le=50, description="Maximum suggestions"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Get search suggestions for autocomplete.

    Returns entity name suggestions that start with or contain the query.
    Optimized for fast response times for use in search-as-you-type UIs.

    Results prioritize prefix matches over partial matches for better UX.

    **Example:** `?q=lac` returns "Lactobacillus", "Lachnospira", "Lactococcus", etc.
    """
    kg = get_kg()

    suggestions = []
    q_lower = q.lower()

    # Get taxa suggestions
    taxa_query = f"""
    MATCH (t:Taxon)
    WHERE toLower(t.name) CONTAINS $q_lower
      AND {ORG_FILTER("t")}
    WITH t,
         CASE WHEN toLower(t.name) STARTS WITH $q_lower THEN 0 ELSE 1 END AS priority
    RETURN 'Taxon' AS type,
           t.name AS name,
           t.taxon_id AS id,
           priority
    ORDER BY priority, t.name
    LIMIT $limit
    """

    taxa_results = kg.execute_cypher(taxa_query, {
        "q_lower": q_lower,
        "limit": limit,
        **scope_params(scope),
    })
    suggestions.extend(taxa_results)

    # Get disease suggestions
    disease_query = f"""
    MATCH (d:Disease)
    WHERE toLower(d.name) CONTAINS $q_lower
      AND {ORG_FILTER("d")}
    WITH d,
         CASE WHEN toLower(d.name) STARTS WITH $q_lower THEN 0 ELSE 1 END AS priority
    RETURN 'Disease' AS type,
           d.name AS name,
           d.name_normalized AS id,
           priority
    ORDER BY priority, d.name
    LIMIT $limit
    """

    disease_results = kg.execute_cypher(disease_query, {
        "q_lower": q_lower,
        "limit": limit,
        **scope_params(scope),
    })
    suggestions.extend(disease_results)

    # Get metabolite suggestions
    metabolite_query = f"""
    MATCH (m:Compound)
    WHERE toLower(m.name) CONTAINS $q_lower
      AND {ORG_FILTER("m")}
    WITH m,
         CASE WHEN toLower(m.name) STARTS WITH $q_lower THEN 0 ELSE 1 END AS priority
    RETURN 'Metabolite' AS type,
           m.name AS name,
           coalesce(m.compound_id, m.metabolite_id) AS id,
           priority
    ORDER BY priority, m.name
    LIMIT $limit
    """

    metabolite_results = kg.execute_cypher(metabolite_query, {
        "q_lower": q_lower,
        "limit": limit,
        **scope_params(scope),
    })
    suggestions.extend(metabolite_results)

    # Sort all suggestions by priority, then name, and limit
    suggestions.sort(key=lambda x: (x.get("priority", 1), x.get("name", "")))
    suggestions = suggestions[:limit]

    return SuggestionsResponse(
        query=q,
        suggestions=[
            SearchSuggestion(
                type=s["type"],
                name=s["name"],
                id=s.get("id"),
            )
            for s in suggestions
        ],
    )


@router.get("/search/counts")
async def get_search_counts(
    q: str = Query(..., min_length=2, description="Search query"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Get counts of matching entities by type.

    Returns the number of matches for each entity type (Taxon, Disease, Metabolite)
    without fetching full results. Useful for:
    - Showing faceted search counts in UI
    - Deciding which types to query
    - Previewing result volume before full search

    **Example response:**
    ```json
    {"query": "lacto", "counts": {"Taxon": 1523, "Disease": 0, "Metabolite": 2}, "total": 1525}
    ```
    """
    kg = get_kg()

    counts = {}
    q_lower = q.lower()

    # Count taxa
    taxa_count = kg.execute_cypher(
        f"""
        MATCH (t:Taxon)
        WHERE (toLower(t.name) CONTAINS $q_lower OR toLower(t.common_name) CONTAINS $q_lower)
          AND {ORG_FILTER("t")}
        RETURN count(t) AS count
        """,
        {"q_lower": q_lower, **scope_params(scope)},
    )
    counts["Taxon"] = taxa_count[0]["count"] if taxa_count else 0

    # Count diseases
    disease_count = kg.execute_cypher(
        f"""
        MATCH (d:Disease)
        WHERE toLower(d.name) CONTAINS $q_lower
          AND {ORG_FILTER("d")}
        RETURN count(d) AS count
        """,
        {"q_lower": q_lower, **scope_params(scope)},
    )
    counts["Disease"] = disease_count[0]["count"] if disease_count else 0

    # Count metabolites
    metabolite_count = kg.execute_cypher(
        f"""
        MATCH (m:Compound)
        WHERE toLower(m.name) CONTAINS $q_lower
          AND {ORG_FILTER("m")}
        RETURN count(m) AS count
        """,
        {"q_lower": q_lower, **scope_params(scope)},
    )
    counts["Metabolite"] = metabolite_count[0]["count"] if metabolite_count else 0

    return {
        "query": q,
        "counts": counts,
        "total": sum(counts.values()),
    }
