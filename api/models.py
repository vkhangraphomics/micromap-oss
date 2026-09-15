"""
Pydantic models for API request/response schemas.
"""

from typing import List, Optional, Any, Dict
from pydantic import BaseModel, ConfigDict, Field


class KGModel(BaseModel):
    """Base for response models that carry identifiers sourced directly from
    Neo4j. The knowledge graph stores some ids loosely typed — e.g. a handful
    of ``Taxon.ncbi_tax_id`` values are integers while the vast majority are
    strings — and Pydantic v2 will not coerce ``int`` into a ``str`` field by
    default, so a single integer-typed id in a result page would raise a
    ``ValidationError`` and 500 the whole response. ``coerce_numbers_to_str``
    makes ``str`` fields accept numeric values, keeping id-bearing endpoints
    resilient to that inconsistency (see #255)."""

    model_config = ConfigDict(coerce_numbers_to_str=True)


# =============================================================================
# COMMON MODELS
# =============================================================================

class PaginatedResponse(BaseModel):
    """Base model for paginated responses."""
    total: int = Field(..., description="Total number of items")
    limit: int = Field(..., description="Maximum items per page")
    offset: int = Field(..., description="Current offset")


class SearchResult(BaseModel):
    """A single search result."""
    type: str = Field(..., description="Entity type (Taxon, Disease, Metabolite)")
    id: str = Field(..., description="Entity ID")
    name: str = Field(..., description="Entity name")
    rank: Optional[str] = Field(None, description="Taxonomic rank (for taxa)")
    category: Optional[str] = Field(None, description="Category (for metabolites)")


# =============================================================================
# TAXON MODELS
# =============================================================================

class TaxonSummary(KGModel):
    """Summary of a taxon."""
    taxon_id: Optional[str] = Field(None, description="Taxon ID (e.g., NCBITaxon:239935)")
    ncbi_tax_id: Optional[str] = Field(None, description="NCBI Taxonomy ID")
    name: str = Field(..., description="Scientific name")
    rank: Optional[str] = Field(None, description="Taxonomic rank")
    kingdom: Optional[str] = Field(None, description="Kingdom")
    phylum: Optional[str] = Field(None, description="Phylum")
    family: Optional[str] = Field(None, description="Family")
    genus: Optional[str] = Field(None, description="Genus")


class TaxonDetail(TaxonSummary):
    """Detailed taxon information."""
    common_name: Optional[str] = Field(None, description="Common name")
    class_: Optional[str] = Field(None, alias="class", description="Class")
    order: Optional[str] = Field(None, description="Order")
    species: Optional[str] = Field(None, description="Species")


class TaxonDiseaseAssociation(BaseModel):
    """A taxon-disease association."""
    disease_name: str = Field(..., description="Disease name")
    disease_id: Optional[str] = Field(None, description="Disease ID")
    direction: Optional[str] = Field(None, description="Direction (increased, decreased)")
    outcome: Optional[str] = Field(None, description="Qualitative outcome")
    sources: Optional[List[str]] = Field(None, description="Data sources")
    effect_size: Optional[float] = Field(None, description="Effect size of association")
    p_value: Optional[float] = Field(None, description="Statistical p-value")
    evidence_level: Optional[str] = Field(None, description="Evidence level (high, medium, low)")
    n_studies: Optional[int] = Field(None, description="Number of supporting studies")
    pmids: Optional[List[str]] = Field(None, description="PubMed IDs of supporting papers")


class TaxonMetabolite(BaseModel):
    """A metabolite produced by a taxon."""
    metabolite_name: str = Field(..., description="Metabolite name")
    metabolite_id: Optional[str] = Field(None, description="Metabolite ID")
    category: Optional[str] = Field(None, description="Metabolite category")
    sources: Optional[List[str]] = Field(None, description="Data sources")


class TaxaListResponse(PaginatedResponse):
    """Response for taxa list endpoint."""
    taxa: List[Dict[str, Any]] = Field(..., description="List of taxa")


class TaxonChildrenResponse(BaseModel):
    """Response for taxon children endpoint."""
    parent_id: str = Field(..., description="Parent taxon ID")
    children: List[Dict[str, Any]] = Field(..., description="Child taxa")
    count: int = Field(..., description="Number of children")


class TaxonDiseasesResponse(BaseModel):
    """Response for taxon diseases endpoint."""
    taxon_id: str = Field(..., description="Taxon ID")
    diseases: List[TaxonDiseaseAssociation] = Field(..., description="Associated diseases")
    count: int = Field(..., description="Number of associations")


class TaxonMetabolitesResponse(BaseModel):
    """Response for taxon metabolites endpoint."""
    taxon_id: str = Field(..., description="Taxon ID")
    metabolites: List[TaxonMetabolite] = Field(..., description="Produced metabolites")
    count: int = Field(..., description="Number of metabolites")


# =============================================================================
# DISEASE MODELS
# =============================================================================

class DiseaseSummary(BaseModel):
    """Summary of a disease."""
    id: Optional[str] = Field(None, description="Disease ID (normalized name)")
    name: str = Field(..., description="Disease name")
    taxa_count: int = Field(0, description="Number of associated taxa")
    sources: Optional[List[str]] = Field(None, description="Data sources")


class DiseaseDetail(DiseaseSummary):
    """Detailed disease information."""
    top_taxa: Optional[List[Dict[str, Any]]] = Field(None, description="Top associated taxa")


class DiseaseTaxon(KGModel):
    """A taxon associated with a disease."""
    taxon_name: str = Field(..., description="Taxon name")
    taxon_id: Optional[str] = Field(None, description="Taxon ID")
    ncbi_id: Optional[str] = Field(None, description="NCBI Taxonomy ID")
    rank: Optional[str] = Field(None, description="Taxonomic rank")
    direction: Optional[str] = Field(None, description="Direction (increased, decreased)")
    outcome: Optional[str] = Field(None, description="Qualitative outcome")
    effect_size: Optional[float] = Field(None, description="Effect size of association")
    p_value: Optional[float] = Field(None, description="Statistical p-value")
    evidence_level: Optional[str] = Field(None, description="Evidence level (high, medium, low)")
    n_studies: Optional[int] = Field(None, description="Number of supporting studies")
    pmids: Optional[List[str]] = Field(None, description="PubMed IDs of supporting papers")
    sources: Optional[List[str]] = Field(None, description="Data sources")


class RelatedDisease(BaseModel):
    """A related disease (shared microbiome signature)."""
    disease_name: str = Field(..., description="Disease name")
    disease_id: Optional[str] = Field(None, description="Disease ID")
    shared_taxa: int = Field(..., description="Number of shared taxa")


class DiseasesListResponse(PaginatedResponse):
    """Response for diseases list endpoint."""
    diseases: List[DiseaseSummary] = Field(..., description="List of diseases")


class DiseaseTaxaResponse(BaseModel):
    """Response for disease taxa endpoint."""
    disease: str = Field(..., description="Disease name or ID")
    taxa: List[DiseaseTaxon] = Field(..., description="Associated taxa")
    count: int = Field(..., description="Number of taxa")


class RelatedDiseasesResponse(BaseModel):
    """Response for related diseases endpoint."""
    disease_id: str = Field(..., description="Source disease ID")
    related: List[RelatedDisease] = Field(..., description="Related diseases")
    count: int = Field(..., description="Number of related diseases")


# =============================================================================
# METABOLITE MODELS
# =============================================================================

class MetaboliteSummary(BaseModel):
    """Summary of a metabolite."""
    id: Optional[str] = Field(None, description="Metabolite ID")
    name: str = Field(..., description="Metabolite name")
    hmdb_id: Optional[str] = Field(None, description="HMDB ID")
    kegg_id: Optional[str] = Field(None, description="KEGG ID")
    category: Optional[str] = Field(None, description="Category")
    producer_count: int = Field(0, description="Number of producer taxa")


class MetaboliteDetail(MetaboliteSummary):
    """Detailed metabolite information."""
    description: Optional[str] = Field(None, description="Description")
    top_producers: Optional[List[Dict[str, Any]]] = Field(None, description="Top producer taxa")


class MetaboliteProducer(KGModel):
    """A taxon that produces a metabolite."""
    taxon_name: str = Field(..., description="Taxon name")
    taxon_id: Optional[str] = Field(None, description="Taxon ID")
    ncbi_id: Optional[str] = Field(None, description="NCBI Taxonomy ID")
    rank: Optional[str] = Field(None, description="Taxonomic rank")
    metabolite_name: Optional[str] = Field(None, description="Metabolite name")
    evidence_level: Optional[str] = Field(None, description="Evidence level (high, medium, low)")
    pathway_id: Optional[str] = Field(None, description="Associated pathway ID")
    yield_value: Optional[float] = Field(None, alias="yield", description="Production yield")
    notes: Optional[str] = Field(None, description="Additional notes")
    source: Optional[str] = Field(None, description="Data source")


class MetabolitesListResponse(PaginatedResponse):
    """Response for metabolites list endpoint."""
    metabolites: List[MetaboliteSummary] = Field(..., description="List of metabolites")


class MetaboliteProducersResponse(BaseModel):
    """Response for metabolite producers endpoint."""
    metabolite: str = Field(..., description="Metabolite name or ID")
    producers: List[MetaboliteProducer] = Field(..., description="Producer taxa")
    count: int = Field(..., description="Number of producers")


# =============================================================================
# SEARCH MODELS
# =============================================================================

class SearchResponse(BaseModel):
    """Response for search endpoint."""
    query: str = Field(..., description="Search query")
    types: Optional[List[str]] = Field(None, description="Filtered entity types")
    results: List[SearchResult] = Field(..., description="Search results")
    count: int = Field(..., description="Number of results")


class SearchSuggestion(BaseModel):
    """A search suggestion."""
    type: str = Field(..., description="Entity type")
    name: str = Field(..., description="Entity name")
    id: Optional[str] = Field(None, description="Entity ID")


class SuggestionsResponse(BaseModel):
    """Response for suggestions endpoint."""
    query: str = Field(..., description="Search query")
    suggestions: List[SearchSuggestion] = Field(..., description="Suggestions")


# =============================================================================
# STATISTICS MODELS
# =============================================================================

class NodeCounts(BaseModel):
    """Node counts in the knowledge graph."""
    taxa: int = Field(..., description="Number of taxa")
    diseases: int = Field(..., description="Number of diseases")
    metabolites: int = Field(..., description="Number of metabolites")
    pathways: int = Field(0, description="Number of pathways")
    drugs: int = Field(0, description="Number of drugs")
    genes: int = Field(0, description="Number of genes")
    proteins: int = Field(0, description="Number of proteins")
    papers: int = Field(0, description="Number of papers")


class RelationshipCounts(BaseModel):
    """Relationship counts in the knowledge graph."""
    disease_associations: int = Field(..., description="Taxon-disease associations")
    produces: int = Field(..., description="Taxon-metabolite relationships")
    taxonomy_hierarchy: int = Field(..., description="Parent-child relationships")
    participates_in: int = Field(0, description="Metabolite-pathway relationships")
    targets: int = Field(0, description="Drug-target relationships")
    mentioned_in: int = Field(0, description="Entity-paper relationships")
    linked_to_disease: int = Field(0, description="Metabolite-disease associations")
    effective_against: int = Field(0, description="Antimicrobial resistance relationships")
    belongs_to_class: int = Field(0, description="Drug classification relationships")
    processes: int = Field(0, description="Metabolic processing relationships")
    same_as: int = Field(0, description="Entity equivalence relationships")


class StatisticsResponse(BaseModel):
    """Response for statistics endpoint."""
    nodes: NodeCounts = Field(..., description="Node counts")
    relationships: RelationshipCounts = Field(..., description="Relationship counts")
    total_nodes: int = Field(..., description="Total nodes")
    total_relationships: int = Field(..., description="Total relationships")


# =============================================================================
# LINEAGE MODELS
# =============================================================================

class LineageNode(KGModel):
    """A node in the taxonomic lineage."""
    taxon_id: Optional[str] = Field(None, description="Taxon ID")
    ncbi_tax_id: Optional[str] = Field(None, description="NCBI Taxonomy ID")
    name: str = Field(..., description="Scientific name")
    rank: Optional[str] = Field(None, description="Taxonomic rank")


class TaxonLineageResponse(BaseModel):
    """Response for taxon lineage endpoint."""
    taxon_id: str = Field(..., description="Query taxon ID")
    taxon_name: str = Field(..., description="Query taxon name")
    lineage: List[LineageNode] = Field(..., description="Lineage from species to domain")
    depth: int = Field(..., description="Number of levels in lineage")


# =============================================================================
# COMPARISON MODELS
# =============================================================================

class TaxonMetaboliteProfile(BaseModel):
    """Metabolite production profile for a taxon."""
    taxon_id: str = Field(..., description="Taxon ID")
    taxon_name: str = Field(..., description="Taxon name")
    metabolites: List[str] = Field(..., description="List of produced metabolites")
    count: int = Field(..., description="Number of metabolites")


class TaxaCompareResponse(BaseModel):
    """Response for taxa comparison endpoint."""
    taxa: List[TaxonMetaboliteProfile] = Field(..., description="Metabolite profiles per taxon")
    shared_metabolites: List[str] = Field(..., description="Metabolites produced by all taxa")
    all_metabolites: List[str] = Field(..., description="All unique metabolites across taxa")


class DiseaseMicrobiomeProfile(BaseModel):
    """Microbiome profile for a disease."""
    disease_id: str = Field(..., description="Disease ID")
    disease_name: str = Field(..., description="Disease name")
    enriched_taxa: List[str] = Field(..., description="Taxa enriched in disease")
    depleted_taxa: List[str] = Field(..., description="Taxa depleted in disease")
    total_taxa: int = Field(..., description="Total associated taxa")


class DiseasesCompareResponse(BaseModel):
    """Response for diseases comparison endpoint."""
    diseases: List[DiseaseMicrobiomeProfile] = Field(..., description="Microbiome profiles per disease")
    shared_enriched: List[str] = Field(..., description="Taxa enriched in all diseases")
    shared_depleted: List[str] = Field(..., description="Taxa depleted in all diseases")
    unique_to_each: Dict[str, List[str]] = Field(..., description="Taxa unique to each disease")


# =============================================================================
# DISEASE METABOLITES MODEL
# =============================================================================

class DiseaseMetabolite(BaseModel):
    """A metabolite associated with a disease via producing taxa."""
    metabolite_name: str = Field(..., description="Metabolite name")
    metabolite_id: Optional[str] = Field(None, description="Metabolite ID")
    producer_count: int = Field(..., description="Number of disease-associated taxa that produce this")
    producers: List[str] = Field(..., description="Names of producing taxa")
    direction: Optional[str] = Field(None, description="Predominant direction of producers (enriched/depleted)")


class DiseaseMetabolitesResponse(BaseModel):
    """Response for disease metabolites endpoint."""
    disease: str = Field(..., description="Disease name or ID")
    metabolites: List[DiseaseMetabolite] = Field(..., description="Metabolites via associated taxa")
    count: int = Field(..., description="Number of metabolites")


# =============================================================================
# METABOLITE DISEASES MODEL
# =============================================================================

class MetaboliteDisease(BaseModel):
    """A disease linked to a metabolite via producing taxa."""
    disease_name: str = Field(..., description="Disease name")
    disease_id: Optional[str] = Field(None, description="Disease ID")
    producer_count: int = Field(..., description="Number of producers associated with this disease")
    directions: Dict[str, int] = Field(..., description="Count of enriched vs depleted producers")
