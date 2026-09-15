"""
Base loader class for all data ingestion pipelines.

Provides common functionality for:
- Neo4j connection management
- Batch processing
- Progress tracking
- Error handling
- Multi-tenant support
"""

from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from neo4j import Driver
import logging
import time
import re

logger = logging.getLogger(__name__)


# Common abbreviation mappings for disease names
DISEASE_ABBREVIATIONS = {
    "mdd": "major depressive disorder",
    "ibd": "inflammatory bowel disease",
    "ibs": "irritable bowel syndrome",
    "t2d": "type 2 diabetes",
    "t2dm": "type 2 diabetes",
    "crc": "colorectal cancer",
    "nafld": "non-alcoholic fatty liver disease",
    "ad": "alzheimer's disease",
    "pd": "parkinson's disease",
    "ms": "multiple sclerosis",
    "ra": "rheumatoid arthritis",
    "asd": "autism spectrum disorder",
    "uc": "ulcerative colitis",
    "cd": "crohn's disease",
}


def normalize_disease_name(name: str) -> str:
    """
    Normalize disease name for consistent matching across data sources.

    This function ensures that "Crohn's Disease", "Crohn's disease",
    "crohn's disease", "CD", and "Crohns disease" all map to the same
    normalized key.

    Handles:
    - Case normalization
    - Abbreviation expansion (e.g., IBD, MDD, T2D)
    - Punctuation removal (apostrophes, hyphens)
    - Common suffix/prefix normalization (disease, disorder, syndrome)
    - Whitespace normalization

    Args:
        name: Disease name from any source

    Returns:
        Normalized lowercase string suitable for MERGE key
    """
    if not name:
        return ""
    # Strip whitespace, convert to lowercase
    normalized = name.strip().lower()
    # Remove extra whitespace
    normalized = re.sub(r'\s+', ' ', normalized)

    # Check if the entire name is a known abbreviation
    if normalized in DISEASE_ABBREVIATIONS:
        normalized = DISEASE_ABBREVIATIONS[normalized]

    # Remove possessive apostrophes and standalone apostrophes
    # "crohn's" -> "crohns", "alzheimer's" -> "alzheimers"
    normalized = normalized.replace("'", "").replace("\u2019", "")

    # Normalize hyphens to spaces for consistency
    # "non-alcoholic" -> "non alcoholic"
    normalized = normalized.replace("-", " ")

    # Remove extra whitespace again after substitutions
    normalized = re.sub(r'\s+', ' ', normalized).strip()

    return normalized


def generate_disease_id(name: str, identifiers: Dict[str, str] = None) -> str:
    """
    Generate a stable disease ID, preferring standard identifiers.

    Priority order:
    1. DOID (Disease Ontology ID)
    2. MeSH ID
    3. OMIM ID
    4. UMLS CUI
    5. ICD-10 code
    6. Normalized name-based ID (fallback)

    Args:
        name: Disease name
        identifiers: Dict of available identifiers (doid, mesh_id, omim_id, umls_cui, icd10)

    Returns:
        Stable disease identifier string
    """
    if identifiers:
        if identifiers.get('doid'):
            return f"DOID:{identifiers['doid']}"
        if identifiers.get('mesh_id'):
            return f"MESH:{identifiers['mesh_id']}"
        if identifiers.get('omim_id'):
            return f"OMIM:{identifiers['omim_id']}"
        if identifiers.get('umls_cui'):
            return f"UMLS:{identifiers['umls_cui']}"
        if identifiers.get('icd10'):
            return f"ICD10:{identifiers['icd10']}"

    # Fallback: use normalized name
    normalized = normalize_disease_name(name)
    return f"disease:{normalized.replace(' ', '_')}"


@dataclass
class LoaderStats:
    """Statistics for a data loading operation."""
    source: str
    # Use UTC-aware timestamps everywhere so duration math, isoformat()
    # serialization, and downstream cross-instance comparisons all agree
    # regardless of server timezone or DST. completed_at must also be set
    # with timezone.utc (see _process_pipeline) for the subtraction in
    # duration_seconds to work.
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    nodes_created: int = 0
    nodes_updated: int = 0
    relationships_created: int = 0
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        if self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return (datetime.now(timezone.utc) - self.started_at).total_seconds()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
            "nodes_created": self.nodes_created,
            "nodes_updated": self.nodes_updated,
            "relationships_created": self.relationships_created,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings)
        }


class BaseLoader(ABC):
    """
    Abstract base class for data loaders.

    Subclasses must implement:
    - source_name: Name of the data source
    - extract(): Extract data from source
    - transform(): Transform data to graph format
    - load(): Load data into Neo4j
    """

    def __init__(
        self,
        driver: Driver,
        organization_id: str,
        batch_size: int = 1000,
        database: str = "neo4j"
    ):
        """
        Initialize the loader.

        Args:
            driver: Neo4j driver instance
            organization_id: Organization ID for multi-tenant isolation
            batch_size: Number of records to process per batch
            database: Neo4j database name
        """
        self.driver = driver
        self.organization_id = organization_id
        self.batch_size = batch_size
        self.database = database
        self.stats = LoaderStats(source=self.source_name)

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Name of the data source."""
        pass

    @abstractmethod
    def extract(self, **kwargs) -> Iterator[Dict[str, Any]]:
        """
        Extract data from the source.

        Yields:
            Dictionary records from the source
        """
        pass

    @abstractmethod
    def transform(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Transform a source record to graph format.

        Args:
            record: Raw record from source

        Returns:
            Transformed record ready for loading, or None to skip
        """
        pass

    @abstractmethod
    def load_batch(self, batch: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Load a batch of transformed records into Neo4j.

        Args:
            batch: List of transformed records

        Returns:
            Dictionary with counts of nodes/relationships created
        """
        pass

    def run(self, **extract_kwargs) -> LoaderStats:
        """
        Run the complete ETL pipeline.

        Args:
            **extract_kwargs: Arguments passed to extract()

        Returns:
            Statistics about the loading operation
        """
        logger.info(f"Starting {self.source_name} data ingestion for org {self.organization_id}")
        self.stats = LoaderStats(source=self.source_name)

        try:
            batch = []
            total_processed = 0

            for record in self.extract(**extract_kwargs):
                try:
                    transformed = self.transform(record)
                    if transformed:
                        batch.append(transformed)
                except Exception as e:
                    self.stats.errors.append(f"Transform error: {str(e)}")
                    logger.warning(f"Transform error: {e}")

                if len(batch) >= self.batch_size:
                    self._process_batch(batch)
                    total_processed += len(batch)
                    batch = []

                    if total_processed % 10000 == 0:
                        logger.info(f"Processed {total_processed} records...")

            # Process remaining batch
            if batch:
                self._process_batch(batch)
                total_processed += len(batch)

            # Post-pass, after every node in the source exists. A loader whose
            # relationships point at records that may arrive in a LATER batch
            # cannot resolve them per-batch — the `MATCH` silently drops the row
            # and nothing retries it. That is #273: 29% of the taxonomy lost its
            # HAS_PARENT edge that way. Default is a no-op, so loaders whose
            # edges are self-contained per batch are unaffected.
            self.stats.relationships_created += self.finalize()

            self.stats.completed_at = datetime.now(timezone.utc)
            logger.info(f"Completed {self.source_name} ingestion: {self.stats.to_dict()}")

        except Exception as e:
            self.stats.errors.append(f"Pipeline error: {str(e)}")
            logger.exception("Pipeline error: %s", e)
            raise

        return self.stats

    def finalize(self) -> int:
        """Hook run once after all batches, for edges that cannot be resolved
        per-batch because an endpoint may arrive later (#273).

        Returns the number of relationships created. Default: no-op.
        """
        return 0

    def _process_batch(self, batch: List[Dict[str, Any]]):
        """Process a single batch of records."""
        if not batch:
            return

        try:
            results = self.load_batch(batch)
            self.stats.nodes_created += results.get("nodes_created", 0)
            self.stats.nodes_updated += results.get("nodes_updated", 0)
            self.stats.relationships_created += results.get("relationships_created", 0)
        except Exception as e:
            self.stats.errors.append(f"Load batch error: {str(e)}")
            logger.exception("Load batch error: %s", e)

    def execute_cypher(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
        write: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Execute a Cypher query.

        Args:
            query: Cypher query string
            parameters: Query parameters
            write: Whether this is a write transaction

        Returns:
            Query results as list of dictionaries
        """
        with self.driver.session(database=self.database) as session:
            if write:
                result = session.execute_write(
                    lambda tx: list(tx.run(query, parameters or {}))
                )
            else:
                result = session.execute_read(
                    lambda tx: list(tx.run(query, parameters or {}))
                )
            return [dict(record) for record in result]

    def batch_merge_nodes(
        self,
        label: str,
        records: List[Dict[str, Any]],
        merge_key: str,
        set_properties: Optional[List[str]] = None
    ) -> int:
        """
        Batch merge nodes using UNWIND.

        Args:
            label: Node label
            records: List of property dictionaries
            merge_key: Property to use for MERGE matching
            set_properties: Properties to SET (all if None)

        Returns:
            Number of nodes processed
        """
        if not records:
            return 0

        # Add organization_id to all records
        for record in records:
            record["organization_id"] = self.organization_id

        # Build SET clause
        if set_properties:
            set_clause = ", ".join([f"n.{p} = record.{p}" for p in set_properties])
        else:
            set_clause = "n += record"

        query = f"""
            UNWIND $records AS record
            MERGE (n:{label} {{{merge_key}: record.{merge_key}}})
            ON CREATE SET {set_clause}, n.created_at = datetime()
            ON MATCH SET {set_clause}, n.updated_at = datetime()
            RETURN count(n) AS count
        """

        result = self.execute_cypher(query, {"records": records})
        return result[0]["count"] if result else 0

    # `batch_create_relationships()` used to live here. It was a generic
    # UNWIND/MATCH/MERGE helper meant for subclass loaders to call instead
    # of hand-writing Cypher, but no subclass ever did — every loader (taxa,
    # diseases, produces, drugbank, chembl, pubmed, ...) writes its own
    # MERGE pattern tailored to its rel type. The helper also had a
    # behavior bug (`SET r += record` would copy from_id/to_id onto the
    # relationship as properties), tracked in #163. Caller grep showed
    # zero references; live-KG check showed zero rels with from_id/to_id
    # keys — confirming both that nobody calls it and that the bug never
    # affected production data. Removed rather than fixed.


class FileBasedLoader(BaseLoader):
    """Base class for loaders that read from files."""

    def __init__(
        self,
        driver: Driver,
        organization_id: str,
        file_path: str,
        batch_size: int = 1000,
        database: str = "neo4j"
    ):
        super().__init__(driver, organization_id, batch_size, database)
        self.file_path = file_path

    def validate_file(self) -> bool:
        """Check if the source file exists and is readable."""
        import os
        if not os.path.exists(self.file_path):
            raise FileNotFoundError(f"Source file not found: {self.file_path}")
        return True


class APIBasedLoader(BaseLoader):
    """Base class for loaders that fetch from APIs."""

    def __init__(
        self,
        driver: Driver,
        organization_id: str,
        api_base_url: str,
        batch_size: int = 1000,
        database: str = "neo4j",
        rate_limit_delay: float = 0.5
    ):
        super().__init__(driver, organization_id, batch_size, database)
        self.api_base_url = api_base_url
        self.rate_limit_delay = rate_limit_delay

    def fetch_with_retry(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        max_retries: int = 3,
        method: str = "GET",
        json: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Fetch data from API with retry logic.

        Supports both GET (query ``params``) and POST (``json`` body). Some
        upstream APIs (e.g. GMrepo's Django backend) only accept POST and
        require a JSON body; pass ``method="POST"`` with ``json=...`` for those.
        """
        import requests

        method = method.upper()
        for attempt in range(max_retries):
            try:
                time.sleep(self.rate_limit_delay)
                if method == "POST":
                    response = requests.post(url, params=params, json=json, timeout=30)
                else:
                    response = requests.get(url, params=params, timeout=30)
                response.raise_for_status()
                return response.json()
            except requests.RequestException as e:
                if attempt == max_retries - 1:
                    raise
                logger.warning(f"API request failed (attempt {attempt + 1}): {e}")
                time.sleep(2 ** attempt)  # Exponential backoff

        return {}
