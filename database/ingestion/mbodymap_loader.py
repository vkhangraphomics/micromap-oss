"""
mBodyMap Data Loader

Loads body-site-specific microbiome reference data from mBodyMap into Neo4j.
Introduces a BodySite node type and FOUND_IN relationships between Taxon and BodySite.

Data source: mBodyMap - a curated database of body-site-specific microbiome profiles.
"""

import csv
import logging
from typing import Optional, Iterator, Dict, Any, List

from .base_loader import FileBasedLoader

logger = logging.getLogger(__name__)


class MBodyMapLoader(FileBasedLoader):
    """
    Load mBodyMap body-site-specific microbiome reference data into Neo4j.

    Creates:
    - Taxon nodes (MERGE by taxon_id)
    - BodySite nodes (MERGE by body_site_id) - NEW node type
    - FOUND_IN relationships (Taxon -> BodySite) with abundance/prevalence metadata
    """

    @property
    def source_name(self) -> str:
        return "mBodyMap"

    def __init__(
        self,
        driver,
        organization_id: str,
        file_path: str,
        batch_size: int = 500,
        database: str = "neo4j",
    ):
        """
        Initialize the mBodyMap loader.

        Args:
            driver: Neo4j driver
            organization_id: Organization ID for multi-tenant isolation
            file_path: Path to mBodyMap TSV/CSV file
            batch_size: Records per batch
            database: Neo4j database name
        """
        super().__init__(driver, organization_id, file_path, batch_size, database)

    def extract(self, **kwargs) -> Iterator[Dict[str, Any]]:
        """
        Extract data from mBodyMap export file (TSV or CSV).

        Expected columns:
        - taxon_name: Organism name
        - ncbi_tax_id: NCBI Taxonomy ID (if available)
        - body_site: Body site name (e.g., Gut, Oral, Skin)
        - relative_abundance: Relative abundance value
        - prevalence: Prevalence across samples
        - health_status: Health status context (e.g., healthy, diseased)

        Yields:
            Dictionary records from the source file
        """
        logger.info(f"Loading mBodyMap data from {self.file_path}")

        with open(self.file_path, "r", encoding="utf-8") as f:
            # Detect delimiter
            first_line = f.readline()
            f.seek(0)
            delimiter = "\t" if "\t" in first_line else ","

            reader = csv.DictReader(f, delimiter=delimiter)
            for row in reader:
                yield row

    def transform(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Transform an mBodyMap record to graph format.

        Args:
            record: Raw record from source file

        Returns:
            Transformed record ready for loading, or None to skip
        """
        taxon_name = (record.get("taxon_name") or "").strip()
        ncbi_tax_id = (record.get("ncbi_tax_id") or "").strip()
        body_site = (record.get("body_site") or "").strip()

        # Skip records without taxon info or body site
        if not taxon_name and not ncbi_tax_id:
            return None
        if not body_site:
            return None

        # Generate taxon_id
        if ncbi_tax_id:
            taxon_id = f"NCBITaxon:{ncbi_tax_id}"
        else:
            taxon_id = f"mbodymap:{taxon_name.replace(' ', '_').lower()}"

        # Parse numeric fields safely
        relative_abundance = None
        try:
            relative_abundance = float(record.get("relative_abundance", ""))
        except (ValueError, TypeError):
            relative_abundance = None

        prevalence = None
        try:
            prevalence = float(record.get("prevalence", ""))
        except (ValueError, TypeError):
            prevalence = None

        # Normalize body site
        body_site_normalized = body_site.lower()
        body_site_id = f"bodysite:{body_site_normalized}"

        health_status = (record.get("health_status") or "").strip()

        return {
            "taxon_name": taxon_name or ncbi_tax_id,
            "taxon_id": taxon_id,
            "ncbi_tax_id": ncbi_tax_id,
            "body_site": body_site,
            "body_site_normalized": body_site_normalized,
            "body_site_id": body_site_id,
            "relative_abundance": relative_abundance,
            "prevalence": prevalence,
            "health_status": health_status,
            "source": "mBodyMap",
            "organization_id": self.organization_id,
        }

    def load_batch(self, batch: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Load a batch of mBodyMap records into Neo4j.

        Creates:
        - Taxon nodes (MERGE by taxon_id)
        - BodySite nodes (MERGE by body_site_id)
        - FOUND_IN relationships (Taxon -> BodySite)
        """
        if not batch:
            return {"nodes_created": 0, "relationships_created": 0}

        nodes_created = 0
        rels_created = 0

        # Load Taxon nodes
        nodes_created += self._load_taxa(batch)

        # Load BodySite nodes
        nodes_created += self._load_body_sites(batch)

        # Load FOUND_IN relationships
        rels_created += self._load_found_in(batch)

        return {
            "nodes_created": nodes_created,
            "relationships_created": rels_created,
        }

    def _load_taxa(self, batch: List[Dict[str, Any]]) -> int:
        """Load Taxon nodes from batch."""
        taxa = {}
        for record in batch:
            taxon_id = record["taxon_id"]
            if taxon_id not in taxa:
                taxa[taxon_id] = {
                    "taxon_id": taxon_id,
                    "name": record["taxon_name"],
                    "ncbi_tax_id": record.get("ncbi_tax_id"),
                    "organization_id": self.organization_id,
                }

        query = """
            UNWIND $taxa AS t
            MERGE (taxon:Taxon {taxon_id: t.taxon_id})
            ON CREATE SET
                taxon.name = t.name,
                taxon.ncbi_tax_id = t.ncbi_tax_id,
                taxon.organization_id = t.organization_id,
                taxon.sources = ['mBodyMap'],
                taxon.created_at = datetime()
            ON MATCH SET
                taxon.name = COALESCE(taxon.name, t.name),
                taxon.ncbi_tax_id = COALESCE(taxon.ncbi_tax_id, t.ncbi_tax_id),
                taxon.sources = CASE
                    WHEN 'mBodyMap' IN taxon.sources THEN taxon.sources
                    ELSE taxon.sources + 'mBodyMap'
                END,
                taxon.updated_at = datetime()
            RETURN count(taxon) AS count
        """

        result = self.execute_cypher(query, {"taxa": list(taxa.values())})
        return result[0]["count"] if result else 0

    def _load_body_sites(self, batch: List[Dict[str, Any]]) -> int:
        """Load BodySite nodes from batch."""
        body_sites = {}
        for record in batch:
            bs_id = record["body_site_id"]
            if bs_id not in body_sites:
                body_sites[bs_id] = {
                    "body_site_id": bs_id,
                    "name": record["body_site"],
                    "name_normalized": record["body_site_normalized"],
                    "organization_id": self.organization_id,
                }

        query = """
            UNWIND $body_sites AS bs
            MERGE (site:BodySite {body_site_id: bs.body_site_id})
            ON CREATE SET
                site.name = bs.name,
                site.name_normalized = bs.name_normalized,
                site.organization_id = bs.organization_id,
                site.created_at = datetime()
            ON MATCH SET
                site.name = COALESCE(site.name, bs.name),
                site.updated_at = datetime()
            RETURN count(site) AS count
        """

        result = self.execute_cypher(query, {"body_sites": list(body_sites.values())})
        return result[0]["count"] if result else 0

    def _load_found_in(self, batch: List[Dict[str, Any]]) -> int:
        """Load FOUND_IN relationships from batch."""
        relationships = []
        for record in batch:
            relationships.append({
                "taxon_id": record["taxon_id"],
                "body_site_id": record["body_site_id"],
                "relative_abundance": record["relative_abundance"],
                "prevalence": record["prevalence"],
                "health_status": record["health_status"],
                "source": record["source"],
            })

        query = """
            UNWIND $relationships AS r
            MATCH (taxon:Taxon {taxon_id: r.taxon_id})
            MATCH (site:BodySite {body_site_id: r.body_site_id})
            MERGE (taxon)-[rel:FOUND_IN]->(site)
            ON CREATE SET
                rel.relative_abundance = r.relative_abundance,
                rel.prevalence = r.prevalence,
                rel.health_status = r.health_status,
                rel.source = r.source,
                rel.created_at = datetime()
            ON MATCH SET
                rel.relative_abundance = COALESCE(r.relative_abundance, rel.relative_abundance),
                rel.prevalence = COALESCE(r.prevalence, rel.prevalence),
                rel.health_status = r.health_status,
                rel.updated_at = datetime()
            RETURN count(rel) AS count
        """

        result = self.execute_cypher(query, {"relationships": relationships})
        return result[0]["count"] if result else 0
