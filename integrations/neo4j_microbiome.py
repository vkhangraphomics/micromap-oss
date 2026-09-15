"""
Neo4j Microbiome Knowledge Graph Integration.

This module provides a high-level interface for interacting with the
Graphomics microbiome knowledge graph in Neo4j.

Features:
- Multi-tenant data isolation
- CRUD operations for microbiome entities
- Efficient batch operations
- Query builders for common patterns
- Transaction management

Usage:
    from integrations.neo4j_microbiome import MicrobiomeKG

    kg = MicrobiomeKG(
        uri="bolt://localhost:7687",
        user="neo4j",
        password="password",
        database="graphomics"
    )

    # Add a taxon
    kg.add_taxon(
        ncbi_id="239935",
        name="Akkermansia muciniphila",
        rank="species",
        lineage=[...],
        organization_id="org_startup_alpha"
    )

    # Link sample to taxa
    kg.add_sample_composition(
        sample_id="SMP001",
        compositions=[
            {"ncbi_id": "239935", "abundance": 0.05, "reads": 2500},
            ...
        ],
        organization_id="org_startup_alpha"
    )
"""

import logging
import os
from typing import List, Dict, Any, Optional
from datetime import date
from contextlib import contextmanager

from neo4j import GraphDatabase, Driver, Session

logger = logging.getLogger(__name__)

# Default Neo4j settings from environment (for Docker compatibility)
_DEFAULT_NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
_DEFAULT_NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
_DEFAULT_NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "password")
_DEFAULT_NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE", "graphomics")


class MicrobiomeKG:
    """
    High-level interface for the Graphomics Microbiome Knowledge Graph.

    This class provides methods to interact with the Neo4j database
    containing microbiome data, clinical metadata, and literature links.
    """

    def __init__(
        self,
        uri: str = None,
        user: str = None,
        password: str = None,
        database: str = None,
        max_connection_pool_size: int = 50
    ):
        """
        Initialize connection to Neo4j.

        Args:
            uri: Neo4j connection URI (defaults to env NEO4J_URI or bolt://localhost:7687)
            user: Username (defaults to env NEO4J_USER or neo4j)
            password: Password (defaults to env NEO4J_PASSWORD or password)
            database: Database name (defaults to env NEO4J_DATABASE or graphomics)
            max_connection_pool_size: Max connection pool size
        """
        # Use environment defaults if not provided
        uri = uri or _DEFAULT_NEO4J_URI
        user = user or _DEFAULT_NEO4J_USER
        password = password or _DEFAULT_NEO4J_PASSWORD
        database = database or _DEFAULT_NEO4J_DATABASE

        self.uri = uri
        self.database = database

        try:
            self.driver: Driver = GraphDatabase.driver(
                uri,
                auth=(user, password),
                max_connection_pool_size=max_connection_pool_size
            )
            logger.info(f"✅ Connected to Neo4j at {uri}")
        except Exception as e:
            logger.exception("❌ Failed to connect to Neo4j: %s", e)
            raise

    def close(self):
        """Close the Neo4j driver connection."""
        if self.driver:
            self.driver.close()
            logger.info("🔌 Closed Neo4j connection")

    @contextmanager
    def session(self) -> Session:
        """Context manager for Neo4j sessions."""
        session = self.driver.session(database=self.database)
        try:
            yield session
        finally:
            session.close()

    def verify_connection(self) -> bool:
        """Verify connection to Neo4j."""
        try:
            with self.session() as session:
                result = session.run("RETURN 1 AS num")
                return result.single()["num"] == 1
        except Exception as e:
            logger.exception("Connection verification failed: %s", e)
            return False

    # =========================================================================
    # TAXON OPERATIONS
    # =========================================================================

    def add_taxon(
        self,
        ncbi_id: str,
        name: str,
        rank: str,
        organization_id: str,
        lineage: Optional[List[str]] = None,
        common_name: Optional[str] = None,
        parent_ncbi_id: Optional[str] = None,
        gram_stain: Optional[str] = None,
        oxygen_requirement: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Add or update a taxon in the knowledge graph.

        Args:
            ncbi_id: NCBI Taxonomy ID (unique identifier)
            name: Scientific name
            rank: Taxonomic rank (domain, phylum, class, order, family, genus, species)
            organization_id: Organization ID for multi-tenancy
            lineage: Full taxonomic lineage (optional)
            common_name: Common name (optional)
            parent_ncbi_id: Parent taxon NCBI ID (optional)
            gram_stain: Gram stain result (positive, negative, variable, unknown)
            oxygen_requirement: Oxygen requirement (aerobic, anaerobic, facultative)
            **kwargs: Additional properties

        Returns:
            Created/updated taxon node properties
        """
        query = """
        MERGE (t:Taxon {ncbi_id: $ncbi_id})
        ON CREATE SET t.created_at = datetime()
        SET t.name = $name,
            t.rank = $rank,
            t.organization_id = $organization_id,
            t.lineage = $lineage,
            t.common_name = $common_name,
            t.parent_ncbi_id = $parent_ncbi_id,
            t.gram_stain = $gram_stain,
            t.oxygen_requirement = $oxygen_requirement,
            t.updated_at = datetime()
        SET t += $extra_props
        RETURN t
        """

        params = {
            "ncbi_id": ncbi_id,
            "name": name,
            "rank": rank,
            "organization_id": organization_id,
            "lineage": lineage or [],
            "common_name": common_name,
            "parent_ncbi_id": parent_ncbi_id,
            "gram_stain": gram_stain,
            "oxygen_requirement": oxygen_requirement,
            "extra_props": kwargs
        }

        with self.session() as session:
            result = session.run(query, params)
            record = result.single()
            if record is None:
                raise ValueError(f"Failed to create/update taxon {ncbi_id}")
            return dict(record["t"])

    def get_taxon_by_ncbi_id(
        self,
        ncbi_id: str,
        organization_id: Optional[str] = None,
        public_orgs: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve a taxon by NCBI Taxonomy ID.

        Args:
            ncbi_id: NCBI Taxonomy ID
            organization_id: Caller's org. None means "public orgs only" (not
                all orgs) — the #200 shared+private predicate.
            public_orgs: List of public organization IDs visible to all (default: ["default"])

        Returns:
            Taxon properties or None if not found
        """
        query = """
        MATCH (t:Taxon {ncbi_id: $ncbi_id})
        WHERE t.organization_id = $organization_id OR t.organization_id IN $public_orgs
        RETURN t
        """

        with self.session() as session:
            result = session.run(query, {
                "ncbi_id": ncbi_id,
                "organization_id": organization_id,
                "public_orgs": public_orgs if public_orgs is not None else ["default"],
            })
            record = result.single()
            return dict(record["t"]) if record else None

    def search_taxa_by_name(
        self,
        name_pattern: str,
        organization_id: Optional[str] = None,
        limit: int = 10,
        public_orgs: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search taxa by name pattern (case-insensitive).

        Args:
            name_pattern: Name pattern (e.g., "Akkerman*")
            organization_id: Filter by organization (optional)
            limit: Max results
            public_orgs: List of public organization IDs visible to all (default: ["default"])

        Returns:
            List of matching taxa
        """
        query = """
        MATCH (t:Taxon)
        WHERE t.name =~ $pattern
          AND (t.organization_id = $organization_id OR t.organization_id IN $public_orgs)
        RETURN t
        ORDER BY t.name
        LIMIT $limit
        """

        params = {
            "pattern": f"(?i).*{name_pattern}.*",
            "organization_id": organization_id,
            "limit": limit,
            "public_orgs": public_orgs if public_orgs is not None else ["default"],
        }

        with self.session() as session:
            result = session.run(query, params)
            return [dict(record["t"]) for record in result]

    # =========================================================================
    # SAMPLE OPERATIONS
    # =========================================================================

    def add_sample(
        self,
        sample_id: str,
        patient_id: str,
        organization_id: str,
        collection_date: date,
        collection_site: str,
        sample_type: str,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Add or update a microbiome sample.

        Args:
            sample_id: Unique sample identifier
            patient_id: Patient identifier
            organization_id: Organization ID
            collection_date: Sample collection date
            collection_site: Collection site (gut, skin, oral, etc.)
            sample_type: Sample type (stool, swab, etc.)
            **kwargs: Additional properties (diversity metrics, sequencing metadata)

        Returns:
            Created/updated sample node properties
        """
        query = """
        MERGE (s:Sample {id: $sample_id})
        ON CREATE SET s.created_at = datetime()
        SET s.patient_id = $patient_id,
            s.organization_id = $organization_id,
            s.collection_date = $collection_date,
            s.collection_site = $collection_site,
            s.sample_type = $sample_type,
            s.updated_at = datetime()
        SET s += $extra_props
        RETURN s
        """

        params = {
            "sample_id": sample_id,
            "patient_id": patient_id,
            "organization_id": organization_id,
            "collection_date": collection_date.isoformat() if isinstance(collection_date, date) else collection_date,
            "collection_site": collection_site,
            "sample_type": sample_type,
            "extra_props": kwargs
        }

        with self.session() as session:
            result = session.run(query, params)
            return dict(result.single()["s"])

    def add_sample_composition(
        self,
        sample_id: str,
        compositions: List[Dict[str, Any]],
        organization_id: str
    ) -> int:
        """
        Add taxonomic composition for a sample (batch operation).

        Args:
            sample_id: Sample identifier
            compositions: List of dicts with keys: ncbi_id, abundance, reads
            organization_id: Organization ID for validation

        Returns:
            Number of relationships created

        Example:
            kg.add_sample_composition(
                sample_id="SMP001",
                compositions=[
                    {"ncbi_id": "239935", "abundance": 0.05, "reads": 2500},
                    {"ncbi_id": "1680", "abundance": 0.08, "reads": 4000}
                ],
                organization_id="org_demo"
            )
        """
        query = """
        MATCH (s:Sample {id: $sample_id, organization_id: $organization_id})
        UNWIND $compositions AS comp
        MATCH (t:Taxon {ncbi_id: comp.ncbi_id})
        MERGE (s)-[r:CONTAINS]->(t)
        SET r.abundance = comp.abundance,
            r.reads = comp.reads,
            r.rank = comp.rank,
            r.detection_method = comp.detection_method,
            r.updated_at = datetime()
        RETURN count(r) AS relationships_created
        """

        # Add defaults
        for comp in compositions:
            comp.setdefault("rank", None)
            comp.setdefault("detection_method", "16S_rRNA")

        with self.session() as session:
            result = session.run(query, {
                "sample_id": sample_id,
                "compositions": compositions,
                "organization_id": organization_id
            })
            return result.single()["relationships_created"]

    def get_sample_composition(
        self,
        sample_id: str,
        organization_id: str,
        min_abundance: float = 0.0
    ) -> List[Dict[str, Any]]:
        """
        Get taxonomic composition of a sample.

        Args:
            sample_id: Sample identifier
            organization_id: Organization ID
            min_abundance: Minimum abundance threshold

        Returns:
            List of taxa with abundance data
        """
        query = """
        MATCH (s:Sample {id: $sample_id, organization_id: $organization_id})
              -[r:CONTAINS]->(t:Taxon)
        WHERE r.abundance >= $min_abundance
        RETURN t.ncbi_id AS ncbi_id,
               t.name AS name,
               t.rank AS rank,
               r.abundance AS abundance,
               r.reads AS reads
        ORDER BY r.abundance DESC
        """

        with self.session() as session:
            result = session.run(query, {
                "sample_id": sample_id,
                "organization_id": organization_id,
                "min_abundance": min_abundance
            })
            return [dict(record) for record in result]

    # =========================================================================
    # PATIENT OPERATIONS
    # =========================================================================

    def add_patient(
        self,
        patient_id: str,
        organization_id: str,
        age: int,
        sex: str,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Add or update a patient.

        Args:
            patient_id: Unique patient identifier
            organization_id: Organization ID
            age: Patient age
            sex: Patient sex (M, F, O)
            **kwargs: Additional properties (disease_state, bmi, etc.)

        Returns:
            Created/updated patient node properties
        """
        query = """
        MERGE (p:Patient {id: $patient_id})
        SET p.organization_id = $organization_id,
            p.age = $age,
            p.sex = $sex,
            p.updated_at = datetime()
        SET p += $extra_props
        WITH p
        WHERE p.created_at IS NULL
        SET p.created_at = datetime()
        RETURN p
        """

        params = {
            "patient_id": patient_id,
            "organization_id": organization_id,
            "age": age,
            "sex": sex,
            "extra_props": kwargs
        }

        with self.session() as session:
            result = session.run(query, params)
            return dict(result.single()["p"])

    def link_patient_to_sample(
        self,
        patient_id: str,
        sample_id: str,
        timepoint: int = 0,
        collection_date: Optional[date] = None
    ) -> bool:
        """
        Link a patient to a sample.

        Args:
            patient_id: Patient identifier
            sample_id: Sample identifier
            timepoint: Timepoint (0 = baseline, 1 = followup, etc.)
            collection_date: Collection date

        Returns:
            True if relationship was created
        """
        query = """
        MATCH (p:Patient {id: $patient_id})
        MATCH (s:Sample {id: $sample_id})
        MERGE (p)-[r:HAS_SAMPLE]->(s)
        SET r.timepoint = $timepoint,
            r.collection_date = $collection_date
        RETURN r
        """

        params = {
            "patient_id": patient_id,
            "sample_id": sample_id,
            "timepoint": timepoint,
            "collection_date": collection_date.isoformat() if collection_date else None
        }

        with self.session() as session:
            result = session.run(query, params)
            return result.single() is not None

    # =========================================================================
    # LITERATURE OPERATIONS
    # =========================================================================

    def add_paper(
        self,
        pmid: str,
        doi: str,
        title: str,
        abstract: str,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Add or update a scientific paper.

        Args:
            pmid: PubMed ID
            doi: DOI
            title: Paper title
            abstract: Abstract text
            **kwargs: Additional properties (authors, journal, year, etc.)

        Returns:
            Created/updated paper node properties
        """
        query = """
        MERGE (p:Paper {pmid: $pmid})
        SET p.doi = $doi,
            p.title = $title,
            p.abstract = $abstract,
            p.updated_at = datetime()
        SET p += $extra_props
        WITH p
        WHERE p.created_at IS NULL
        SET p.created_at = datetime()
        RETURN p
        """

        params = {
            "pmid": pmid,
            "doi": doi,
            "title": title,
            "abstract": abstract,
            "extra_props": kwargs
        }

        with self.session() as session:
            result = session.run(query, params)
            return dict(result.single()["p"])

    def link_taxon_to_paper(
        self,
        ncbi_id: str,
        pmid: str,
        relevance_score: float = 1.0,
        context: Optional[str] = None,
        mention_count: int = 1
    ) -> bool:
        """
        Link a taxon to a paper (literature connection).

        Args:
            ncbi_id: Taxon NCBI ID
            pmid: Paper PubMed ID
            relevance_score: Relevance score (0-1)
            context: Context/topic (e.g., "obesity", "IBD")
            mention_count: Number of times taxon is mentioned

        Returns:
            True if relationship was created
        """
        query = """
        MATCH (t:Taxon {ncbi_id: $ncbi_id})
        MATCH (p:Paper {pmid: $pmid})
        MERGE (t)-[r:MENTIONED_IN]->(p)
        SET r.relevance_score = $relevance_score,
            r.context = $context,
            r.mention_count = $mention_count,
            r.updated_at = datetime()
        RETURN r
        """

        params = {
            "ncbi_id": ncbi_id,
            "pmid": pmid,
            "relevance_score": relevance_score,
            "context": context,
            "mention_count": mention_count
        }

        with self.session() as session:
            result = session.run(query, params)
            return result.single() is not None

    def get_papers_for_taxon(
        self,
        ncbi_id: str,
        min_relevance: float = 0.5,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get papers mentioning a specific taxon.

        Args:
            ncbi_id: Taxon NCBI ID
            min_relevance: Minimum relevance score
            limit: Max results

        Returns:
            List of papers with relevance data
        """
        query = """
        MATCH (t:Taxon {ncbi_id: $ncbi_id})-[r:MENTIONED_IN]->(p:Paper)
        WHERE r.relevance_score >= $min_relevance
        RETURN p.pmid AS pmid,
               p.doi AS doi,
               p.title AS title,
               p.publication_year AS year,
               r.relevance_score AS relevance_score,
               r.context AS context
        ORDER BY r.relevance_score DESC, p.publication_year DESC
        LIMIT $limit
        """

        with self.session() as session:
            result = session.run(query, {
                "ncbi_id": ncbi_id,
                "min_relevance": min_relevance,
                "limit": limit
            })
            return [dict(record) for record in result]

    # =========================================================================
    # MULTI-TENANCY & SECURITY
    # =========================================================================

    def get_organization_stats(self, organization_id: str) -> Dict[str, int]:
        """
        Get statistics for an organization.

        Args:
            organization_id: Organization ID

        Returns:
            Dict with counts of patients, samples, taxa, papers
        """
        query = """
        MATCH (p:Patient {organization_id: $org_id})
        WITH count(p) AS patients
        MATCH (s:Sample {organization_id: $org_id})
        WITH patients, count(s) AS samples
        MATCH (t:Taxon {organization_id: $org_id})
        WITH patients, samples, count(t) AS taxa
        MATCH (paper:Paper)
        RETURN patients, samples, taxa, count(paper) AS papers
        """

        with self.session() as session:
            result = session.run(query, {"org_id": organization_id})
            return dict(result.single())

    def delete_organization_data(self, organization_id: str) -> Dict[str, int]:
        """
        Delete all data for an organization (use with caution!).

        Args:
            organization_id: Organization ID

        Returns:
            Dict with counts of deleted nodes
        """
        query = """
        MATCH (n {organization_id: $org_id})
        WITH count(n) AS total, labels(n)[0] AS label
        MATCH (n {organization_id: $org_id})
        DETACH DELETE n
        RETURN total, label
        """

        with self.session() as session:
            result = session.run(query, {"org_id": organization_id})
            return {record["label"]: record["total"] for record in result}

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    def execute_cypher(self, query: str, params: Optional[Dict] = None) -> List[Dict]:
        """
        Execute arbitrary Cypher query.

        Args:
            query: Cypher query
            params: Query parameters

        Returns:
            List of result records
        """
        with self.session() as session:
            result = session.run(query, params or {})
            return [dict(record) for record in result]


# Convenience function for quick access
def get_microbiome_kg(
    uri: Optional[str] = None,
    user: Optional[str] = None,
    password: Optional[str] = None,
    database: Optional[str] = None
) -> MicrobiomeKG:
    """
    Get a MicrobiomeKG instance with configuration from env or defaults.

    Args:
        uri: Neo4j URI (defaults to env NEO4J_URI or bolt://localhost:7687)
        user: Username (defaults to env NEO4J_USER or neo4j)
        password: Password (defaults to env NEO4J_PASSWORD)
        database: Database (defaults to env NEO4J_DATABASE or graphomics)

    Returns:
        MicrobiomeKG instance
    """
    import os

    return MicrobiomeKG(
        uri=uri or os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        user=user or os.getenv("NEO4J_USER", "neo4j"),
        password=password or os.getenv("NEO4J_PASSWORD", "password"),
        database=database or os.getenv("NEO4J_DATABASE", "graphomics")
    )
