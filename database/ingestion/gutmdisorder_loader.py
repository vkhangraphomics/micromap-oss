"""
gutMDisorder Data Loader

Loads gut microbiota-disorder association data from the gutMDisorder database into Neo4j.
gutMDisorder is a comprehensive database documenting associations between gut microbes
and disorders/interventions.

Data source: http://bio-annotation.cn/gutMDisorder
"""

from typing import Optional, Iterator, Dict, Any, List
from .base_loader import FileBasedLoader, normalize_disease_name, generate_disease_id
import csv
import logging

logger = logging.getLogger(__name__)

# Map raw alteration terms to standardized directions
DIRECTION_MAP = {
    "increased": "enriched",
    "elevated": "enriched",
    "higher": "enriched",
    "enriched": "enriched",
    "decreased": "depleted",
    "reduced": "depleted",
    "lower": "depleted",
    "depleted": "depleted",
    "altered": "altered",
    "changed": "altered",
}


class GutMDisorderLoader(FileBasedLoader):
    """
    Load gutMDisorder gut microbiota-disorder associations into Neo4j.

    The gutMDisorder database contains:
    - Gut microbe-disorder associations
    - Direction of alteration (increased/decreased/altered)
    - Sample type and evidence type
    - PubMed references
    """

    @property
    def source_name(self) -> str:
        return "gutMDisorder"

    def __init__(
        self,
        driver,
        organization_id: str,
        file_path: str,
        batch_size: int = 500,
        database: str = "neo4j",
    ):
        """
        Initialize the gutMDisorder loader.

        Args:
            driver: Neo4j driver
            organization_id: Organization ID for multi-tenant isolation
            file_path: Path to gutMDisorder export TSV/CSV file
            batch_size: Records per batch
            database: Neo4j database name
        """
        super().__init__(driver, organization_id, file_path, batch_size, database)

    def extract(self, **kwargs) -> Iterator[Dict[str, Any]]:
        """
        Extract data from gutMDisorder export file (TSV or CSV).

        Auto-detects delimiter based on file extension (.tsv -> tab, otherwise comma).

        Yields:
            Dictionary records from the source file
        """
        logger.info(f"Loading gutMDisorder data from {self.file_path}")

        delimiter = "\t" if self.file_path.endswith(".tsv") else ","

        with open(self.file_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            for row in reader:
                yield row

    def transform(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Transform a gutMDisorder record to graph format.

        Args:
            record: Raw record from gutMDisorder file

        Returns:
            Transformed record for Neo4j, or None if essential fields are missing
        """
        # Support both original ("Gut Microbe") and API export ("GutMicrobe") column names
        microbe = (record.get("Gut Microbe") or record.get("GutMicrobe") or "").strip()
        disorder = (record.get("Disorder") or record.get("Condition1") or "").strip()

        if not microbe or not disorder:
            return None

        # Determine taxon ID
        ncbi_id = (record.get("NCBI ID") or record.get("NCBI_Taxonomy_ID") or "").strip()
        if ncbi_id:
            taxon_id = f"NCBITaxon:{ncbi_id}"
        else:
            taxon_id = f"gutmdisorder:{microbe.lower()}"

        # Normalize disease name for consistent entity resolution
        disease_name_normalized = normalize_disease_name(disorder)
        disease_id = generate_disease_id(disorder)

        # Map alteration to standardized direction
        alteration = record.get("Alteration", "").strip().lower()
        direction = DIRECTION_MAP.get(alteration, "altered")

        return {
            "taxon_name": microbe,
            "taxon_id": taxon_id,
            "ncbi_tax_id": ncbi_id if ncbi_id else None,
            "disease_name": disorder,
            "disease_name_normalized": disease_name_normalized,
            "disease_id": disease_id,
            "direction": direction,
            "evidence_type": record.get("Evidence Type", "").strip(),
            "sample_type": record.get("Sample Type", "").strip(),
            "pmid": record.get("PMID", "").strip(),
            "source": "gutMDisorder",
            "organization_id": self.organization_id,
        }

    def load_batch(self, batch: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Load a batch of gutMDisorder records into Neo4j.

        Creates:
        - Taxon nodes (MERGE by taxon_id)
        - Disease nodes (MERGE by name_normalized)
        - ASSOCIATED_WITH_DISEASE relationships
        """
        if not batch:
            return {"nodes_created": 0, "relationships_created": 0}

        nodes_created = 0
        rels_created = 0

        # --- Load Taxa ---
        taxa = {}
        for record in batch:
            tid = record["taxon_id"]
            if tid not in taxa:
                taxa[tid] = {
                    "taxon_id": tid,
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
                taxon.sources = ['gutMDisorder'],
                taxon.created_at = datetime()
            ON MATCH SET
                taxon.name = COALESCE(taxon.name, t.name),
                taxon.ncbi_tax_id = COALESCE(taxon.ncbi_tax_id, t.ncbi_tax_id),
                taxon.sources = CASE
                    WHEN 'gutMDisorder' IN taxon.sources THEN taxon.sources
                    ELSE taxon.sources + 'gutMDisorder'
                END,
                taxon.updated_at = datetime()
            RETURN count(taxon) AS count
        """
        result = self.execute_cypher(query, {"taxa": list(taxa.values())})
        nodes_created += result[0]["count"] if result else 0

        # --- Load Diseases ---
        diseases = {}
        for record in batch:
            norm_name = record["disease_name_normalized"]
            if norm_name and norm_name not in diseases:
                diseases[norm_name] = {
                    "name_normalized": norm_name,
                    "name": record["disease_name"],
                    "disease_id": record["disease_id"],
                    "microbiome_associated": True,
                    "organization_id": self.organization_id,
                    "source": "gutMDisorder",
                }

        query = """
            UNWIND $diseases AS d
            MERGE (disease:Disease {name_normalized: d.name_normalized})
            ON CREATE SET
                disease.name = d.name,
                disease.disease_id = d.disease_id,
                disease.microbiome_associated = d.microbiome_associated,
                disease.organization_id = d.organization_id,
                disease.sources = [d.source],
                disease.created_at = datetime()
            ON MATCH SET
                disease.microbiome_associated = true,
                disease.sources = CASE
                    WHEN d.source IN disease.sources THEN disease.sources
                    ELSE disease.sources + d.source
                END,
                disease.updated_at = datetime()
            RETURN count(disease) AS count
        """
        result = self.execute_cypher(query, {"diseases": list(diseases.values())})
        nodes_created += result[0]["count"] if result else 0

        # --- Load Associations ---
        associations = []
        for record in batch:
            associations.append({
                "taxon_id": record["taxon_id"],
                "disease_name_normalized": record["disease_name_normalized"],
                "direction": record["direction"],
                "evidence_type": record.get("evidence_type"),
                "sample_type": record.get("sample_type"),
                "pmid": record.get("pmid"),
                "source": record["source"],
            })

        query = """
            UNWIND $associations AS a
            MATCH (taxon:Taxon {taxon_id: a.taxon_id})
            MATCH (disease:Disease {name_normalized: a.disease_name_normalized})
            MERGE (taxon)-[r:ASSOCIATED_WITH_DISEASE]->(disease)
            ON CREATE SET
                r.direction = a.direction,
                r.evidence_type = a.evidence_type,
                r.sample_type = a.sample_type,
                r.sources = [a.source],
                r.pmids = CASE WHEN a.pmid IS NOT NULL AND a.pmid <> '' THEN [a.pmid] ELSE [] END,
                r.n_studies = 1,
                r.created_at = datetime()
            ON MATCH SET
                r.sources = CASE
                    WHEN NOT a.source IN r.sources THEN r.sources + a.source
                    ELSE r.sources
                END,
                r.pmids = CASE
                    WHEN a.pmid IS NOT NULL AND a.pmid <> '' AND NOT a.pmid IN r.pmids THEN r.pmids + a.pmid
                    ELSE r.pmids
                END,
                r.n_studies = size(r.pmids),
                r.updated_at = datetime()
            RETURN count(r) AS count
        """
        result = self.execute_cypher(query, {"associations": associations})
        rels_created += result[0]["count"] if result else 0

        return {
            "nodes_created": nodes_created,
            "relationships_created": rels_created,
        }
