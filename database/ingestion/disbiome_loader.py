"""
Disbiome Data Loader

Loads disease-microbiome association data from Disbiome database into Neo4j.
Disbiome contains curated microbiome-disease associations from published literature.

Data source: https://disbiome.ugent.be
"""

from typing import Optional, Iterator, Dict, Any, List
from .base_loader import FileBasedLoader, APIBasedLoader, normalize_disease_name, generate_disease_id
import csv
import json
import logging

logger = logging.getLogger(__name__)


#: #269 Step 2 — backfill the shape of the 125 out-of-band Disbiome :Paper nodes.
#: They carry only pmid/disbiome_sourced/title/year; loader-created papers
#: (pubmed_loader) carry paper_id="PMID:<pmid>", source, created_at. Only touches
#: Disbiome papers still missing paper_id, mints the loader-form id, and preserves
#: any existing source/created_at/organization_id (coalesce). The NOT EXISTS guard
#: refuses to mint an id a real PubMed paper already holds — that would split
#: identity (#267 class) rather than resolve it. Idempotent; never deletes.
BACKFILL_DISBIOME_PAPER_SHAPE_CYPHER = """
MATCH (p:Paper)
WHERE p.disbiome_sourced IS NOT NULL
  AND p.paper_id IS NULL
  AND p.pmid IS NOT NULL
  AND NOT EXISTS {
    MATCH (other:Paper) WHERE other.paper_id = 'PMID:' + toString(p.pmid)
  }
SET p.paper_id = 'PMID:' + toString(p.pmid),
    p.source = coalesce(p.source, 'Disbiome'),
    p.created_at = coalesce(p.created_at, datetime()),
    p.organization_id = coalesce(p.organization_id, 'default')
RETURN count(p) AS repaired
"""


def backfill_disbiome_paper_shape(driver, database: str) -> int:
    """Backfill paper_id/source/created_at on the 125 out-of-band Disbiome
    :Paper nodes so they match the loader shape (#269 Step 2).

    No loader creates these nodes (they were written by ad-hoc operator Cypher
    on 2026-07-14), so `--pubmed` cannot repair them — hence an explicit,
    idempotent backfill. Returns the number of papers repaired.
    """
    with driver.session(database=database) as session:
        record = session.run(BACKFILL_DISBIOME_PAPER_SHAPE_CYPHER).single()
        repaired = record["repaired"] if record else 0

    logger.info(
        "Backfilled Disbiome :Paper shape on %d node(s) in '%s' (#269)",
        repaired, database,
    )
    return repaired


class DisbiomeLoader(FileBasedLoader):
    """
    Load Disbiome disease-microbiome associations into Neo4j.

    The Disbiome database contains:
    - Microorganism-disease associations
    - Direction of change (increased/decreased)
    - Sample type and methodology
    - Evidence from publications
    """

    @property
    def source_name(self) -> str:
        return "Disbiome"

    def __init__(
        self,
        driver,
        organization_id: str,
        file_path: str,
        batch_size: int = 1000,
        database: str = "neo4j"
    ):
        """
        Initialize the Disbiome loader.

        Args:
            driver: Neo4j driver
            organization_id: Organization ID for multi-tenant isolation
            file_path: Path to Disbiome export CSV file
            batch_size: Records per batch
            database: Neo4j database name
        """
        super().__init__(driver, organization_id, file_path, batch_size, database)

        # Cache for mapping names to IDs
        self._taxon_cache: Dict[str, str] = {}
        self._disease_cache: Dict[str, str] = {}

    def extract(self, **kwargs) -> Iterator[Dict[str, Any]]:
        """
        Extract data from Disbiome export (CSV or JSON format).

        CSV expected columns:
        - microorganism: Organism name
        - ncbi_taxid: NCBI Taxonomy ID (if available)
        - disease: Disease name
        - doid: Disease Ontology ID (if available)
        - qualitative_outcome: increased/decreased/altered
        - sample: Sample type (stool, oral, etc.)
        - method: Detection method
        - pmid: PubMed ID
        - doi: Publication DOI

        JSON format (experiments export from Disbiome website):
        - Array of experiment objects with nested organism/disease data
        """
        logger.info(f"Loading Disbiome data from {self.file_path}")

        # Detect file format
        with open(self.file_path, "r", encoding="utf-8") as f:
            first_char = f.read(1)
            f.seek(0)

            if first_char in '[{':
                # JSON format
                yield from self._extract_json(f)
            else:
                # CSV format
                yield from self._extract_csv(f)

    def _extract_csv(self, f) -> Iterator[Dict[str, Any]]:
        """Extract from CSV format."""
        reader = csv.DictReader(f)

        for row in reader:
            yield {
                "microorganism": row.get("microorganism", "").strip(),
                "ncbi_taxid": row.get("ncbi_taxid", "").strip(),
                "taxonomic_rank": row.get("taxonomic_rank", "").strip().lower(),
                "disease": row.get("disease", "").strip(),
                "doid": row.get("doid", "").strip(),
                "icd10": row.get("icd10", "").strip(),
                "qualitative_outcome": row.get("qualitative_outcome", "").strip().lower(),
                "sample_type": row.get("sample", "").strip().lower(),
                "method": row.get("method", "").strip(),
                "pmid": row.get("pmid", "").strip(),
                "doi": row.get("doi", "").strip(),
                "host": row.get("host", "human").strip().lower(),
                "experiment_type": row.get("experiment_type", "").strip(),
            }

    def _extract_json(self, f) -> Iterator[Dict[str, Any]]:
        """
        Extract from JSON format (Disbiome experiments export).

        Handles multiple JSON structures:
        1. Direct array format: [{experiment}, {experiment}, ...]
        2. Wrapped format: {experiments: [...]} or {data: [...]}
        3. Disbiome web export format with flat fields:
           - organism_name, organism_ncbi_id
           - disease_name, meddra_id
           - qualitative_outcome, method_name, sample_name
        """
        data = json.load(f)

        # Handle various JSON structures
        if isinstance(data, list):
            records = data
        elif isinstance(data, dict):
            # Could be {experiments: [...]} or {data: [...]} or single record
            records = data.get("experiments") or data.get("data") or data.get("results") or [data]
        else:
            logger.warning(f"Unexpected JSON format: {type(data)}")
            return

        for record in records:
            # Handle Disbiome web export format (flat structure with organism_name, disease_name)
            if "organism_name" in record:
                # Disbiome web export format
                yield {
                    "microorganism": str(record.get("organism_name") or ""),
                    "ncbi_taxid": str(record.get("organism_ncbi_id") or ""),
                    "taxonomic_rank": "",  # Not provided in this format
                    "disease": str(record.get("disease_name") or ""),
                    "doid": "",  # Not provided
                    "meddra_id": str(record.get("meddra_id") or ""),
                    "icd10": "",
                    "qualitative_outcome": str(record.get("qualitative_outcome") or "").lower(),
                    "sample_type": str(record.get("sample_name") or "").lower(),
                    "method": str(record.get("method_name") or ""),
                    "pmid": str(record.get("publication_id") or ""),  # publication_id is used
                    "doi": "",
                    "host": str(record.get("host_type") or "human").lower(),
                    "experiment_type": "",
                    "experiment_id": record.get("experiment_id"),
                    "disease_id": record.get("disease_id"),
                    "organism_id": record.get("organism_id"),
                }
            else:
                # Handle nested organism/disease objects (generic format)
                organism = record.get("organism") or record.get("microorganism") or {}
                disease = record.get("disease") or {}

                if isinstance(organism, dict):
                    microorganism = organism.get("name", "")
                    ncbi_taxid = str(organism.get("ncbi_id") or organism.get("ncbi_taxid") or "")
                    taxonomic_rank = organism.get("rank", "").lower()
                else:
                    microorganism = str(organism)
                    ncbi_taxid = str(record.get("ncbi_taxid") or record.get("ncbi_id") or "")
                    taxonomic_rank = record.get("taxonomic_rank", "").lower()

                if isinstance(disease, dict):
                    disease_name = disease.get("name", "")
                    meddra_id = str(disease.get("meddra_id") or "")
                    doid = str(disease.get("doid") or "")
                else:
                    disease_name = str(disease)
                    meddra_id = str(record.get("meddra_id") or "")
                    doid = str(record.get("doid") or "")

                yield {
                    "microorganism": microorganism,
                    "ncbi_taxid": ncbi_taxid,
                    "taxonomic_rank": taxonomic_rank,
                    "disease": disease_name,
                    "doid": doid,
                    "meddra_id": meddra_id,
                    "icd10": str(record.get("icd10") or ""),
                    "qualitative_outcome": str(record.get("qualitative_outcome") or record.get("direction") or "").lower(),
                    "sample_type": str(record.get("sample") or record.get("sample_type") or "").lower(),
                    "method": str(record.get("method") or record.get("detection_method") or ""),
                    "pmid": str(record.get("pmid") or record.get("pubmed_id") or ""),
                    "doi": str(record.get("doi") or ""),
                    "host": str(record.get("host") or "human").lower(),
                    "experiment_type": str(record.get("experiment_type") or ""),
                }

    def transform(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Transform Disbiome record to graph format.

        Args:
            record: Raw Disbiome record

        Returns:
            Transformed record for Neo4j
        """
        microorganism = record.get("microorganism")
        disease = record.get("disease")

        if not microorganism or not disease:
            return None

        # Determine taxon ID
        ncbi_taxid = record.get("ncbi_taxid")
        if ncbi_taxid:
            taxon_id = f"NCBITaxon:{ncbi_taxid}"
        else:
            # Use name-based ID for unresolved taxa
            taxon_id = f"disbiome:{microorganism.replace(' ', '_').lower()}"

        # Normalize disease name for consistent entity resolution
        disease_name_normalized = normalize_disease_name(disease)

        # Generate disease ID using standard identifiers if available
        doid = record.get("doid")
        icd10 = record.get("icd10")
        meddra_id = record.get("meddra_id")
        disease_id = generate_disease_id(disease, {
            'doid': doid,
            'icd10': icd10,
            'meddra_id': meddra_id
        })

        # Map qualitative outcome to direction
        outcome = record.get("qualitative_outcome", "").lower()
        if outcome in ["increased", "elevated", "higher", "enriched"]:
            direction = "enriched"
        elif outcome in ["decreased", "reduced", "lower", "depleted"]:
            direction = "depleted"
        else:
            direction = "altered"

        return {
            # Taxon info
            "taxon_id": taxon_id,
            "taxon_name": microorganism,
            "taxon_rank": record.get("taxonomic_rank") or "unknown",
            "ncbi_taxid": ncbi_taxid,

            # Disease info - use normalized name as MERGE key
            "disease_id": disease_id,
            "disease_name": disease,
            "disease_name_normalized": disease_name_normalized,
            "doid": doid,
            "icd10": icd10,
            "meddra_id": meddra_id,

            # Association info
            "direction": direction,
            "qualitative_outcome": outcome,
            "sample_type": record.get("sample_type"),
            "method": record.get("method"),
            "host": record.get("host", "human"),

            # Evidence
            "pmid": record.get("pmid"),
            "doi": record.get("doi"),
            "evidence_level": "curated",
            "source": "disbiome",

            # Multi-tenant
            "organization_id": self.organization_id,
        }

    def load_batch(self, batch: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Load a batch of Disbiome records into Neo4j.

        Creates:
        - Taxon nodes (MERGE by taxon_id)
        - Disease nodes (MERGE by disease_id)
        - ASSOCIATED_WITH_DISEASE relationships
        """
        if not batch:
            return {"nodes_created": 0, "relationships_created": 0}

        nodes_created = 0
        rels_created = 0

        # Load taxa
        nodes_created += self._load_taxa(batch)

        # Load diseases
        nodes_created += self._load_diseases(batch)

        # Load associations
        rels_created += self._load_associations(batch)

        return {
            "nodes_created": nodes_created,
            "relationships_created": rels_created
        }

    def _load_taxa(self, batch: List[Dict[str, Any]]) -> int:
        """Load Taxon nodes from batch."""
        # Deduplicate taxa
        taxa = {}
        for record in batch:
            taxon_id = record["taxon_id"]
            if taxon_id not in taxa:
                taxa[taxon_id] = {
                    "taxon_id": taxon_id,
                    "name": record["taxon_name"],
                    "rank": record["taxon_rank"],
                    "ncbi_tax_id": record.get("ncbi_taxid"),
                    "organization_id": self.organization_id,
                }

        query = """
            UNWIND $taxa AS t
            MERGE (taxon:Taxon {taxon_id: t.taxon_id})
            ON CREATE SET
                taxon.name = t.name,
                taxon.rank = t.rank,
                taxon.ncbi_tax_id = t.ncbi_tax_id,
                taxon.organization_id = t.organization_id,
                taxon.created_at = datetime()
            ON MATCH SET
                taxon.name = COALESCE(taxon.name, t.name),
                taxon.rank = COALESCE(taxon.rank, t.rank),
                taxon.ncbi_tax_id = COALESCE(taxon.ncbi_tax_id, t.ncbi_tax_id),
                taxon.updated_at = datetime()
            RETURN count(taxon) AS count
        """

        result = self.execute_cypher(query, {"taxa": list(taxa.values())})
        return result[0]["count"] if result else 0

    def _load_diseases(self, batch: List[Dict[str, Any]]) -> int:
        """
        Load Disease nodes from batch using normalized name as MERGE key.

        This ensures that "Crohn's Disease" from one source and "crohn's disease"
        from another source merge into a single Disease node, fixing the entity
        resolution problem across data sources.
        """
        # Deduplicate diseases by normalized name
        diseases = {}
        for record in batch:
            norm_name = record["disease_name_normalized"]
            if norm_name and norm_name not in diseases:
                diseases[norm_name] = {
                    "name_normalized": norm_name,
                    "name": record["disease_name"],  # Keep original for display
                    "disease_id": record["disease_id"],
                    "doid": record.get("doid"),
                    "icd10_code": record.get("icd10"),
                    "meddra_id": record.get("meddra_id"),
                    "microbiome_associated": True,
                    "organization_id": self.organization_id,
                    "source": "disbiome",
                }

        query = """
            UNWIND $diseases AS d
            MERGE (disease:Disease {name_normalized: d.name_normalized})
            ON CREATE SET
                disease.name = d.name,
                disease.disease_id = d.disease_id,
                disease.doid = d.doid,
                disease.icd10_code = d.icd10_code,
                disease.meddra_id = d.meddra_id,
                disease.microbiome_associated = d.microbiome_associated,
                disease.organization_id = d.organization_id,
                disease.sources = [d.source],
                disease.created_at = datetime()
            ON MATCH SET
                disease.doid = COALESCE(disease.doid, d.doid),
                disease.icd10_code = COALESCE(disease.icd10_code, d.icd10_code),
                disease.meddra_id = COALESCE(disease.meddra_id, d.meddra_id),
                disease.microbiome_associated = true,
                disease.sources = CASE
                    WHEN d.source IN disease.sources THEN disease.sources
                    ELSE disease.sources + d.source
                END,
                disease.updated_at = datetime()
            RETURN count(disease) AS count
        """

        result = self.execute_cypher(query, {"diseases": list(diseases.values())})
        return result[0]["count"] if result else 0

    def _load_associations(self, batch: List[Dict[str, Any]]) -> int:
        """Load disease-microbiome associations using normalized disease name."""
        associations = []
        for record in batch:
            associations.append({
                "taxon_id": record["taxon_id"],
                "disease_name_normalized": record["disease_name_normalized"],
                "direction": record["direction"],
                "qualitative_outcome": record["qualitative_outcome"],
                "sample_type": record["sample_type"],
                "method": record["method"],
                "pmid": record["pmid"],
                "doi": record["doi"],
                "evidence_level": record["evidence_level"],
                "source": record["source"],
            })

        query = """
            UNWIND $associations AS a
            MATCH (taxon:Taxon {taxon_id: a.taxon_id})
            MATCH (disease:Disease {name_normalized: a.disease_name_normalized})
            MERGE (taxon)-[r:ASSOCIATED_WITH_DISEASE]->(disease)
            ON CREATE SET
                r.direction = a.direction,
                r.qualitative_outcome = a.qualitative_outcome,
                r.sample_type = a.sample_type,
                r.method = a.method,
                r.evidence_level = a.evidence_level,
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
        return result[0]["count"] if result else 0


class GMrepoLoader(APIBasedLoader):
    """
    Load disease-microbiome associations from GMrepo API.

    GMrepo is a curated database containing gut microbiota composition
    data collected from published metagenomic sequencing projects.

    API: https://gmrepo.humangut.info/api
    """

    @property
    def source_name(self) -> str:
        return "GMrepo"

    def __init__(
        self,
        driver,
        organization_id: str,
        batch_size: int = 500,
        database: str = "neo4j"
    ):
        """
        Initialize the GMrepo loader.

        Args:
            driver: Neo4j driver
            organization_id: Organization ID
            batch_size: Records per batch
            database: Neo4j database name
        """
        super().__init__(
            driver,
            organization_id,
            api_base_url="https://gmrepo.humangut.info/api",
            batch_size=batch_size,
            database=database
        )

    def extract(self, diseases: Optional[List[str]] = None, **kwargs) -> Iterator[Dict[str, Any]]:
        """
        Extract disease-microbiome associations from GMrepo API.

        Args:
            diseases: Optional list of disease names to query

        Yields:
            Association records from GMrepo
        """
        # Get list of diseases in GMrepo
        if not diseases:
            diseases_data = self.fetch_with_retry(f"{self.api_base_url}/getallphenotypes")
            diseases = [d["phenotype"] for d in diseases_data.get("phenotypes", [])]

        for disease_name in diseases:
            logger.info(f"Fetching GMrepo data for: {disease_name}")
            try:
                # Get associated taxa for this disease
                response = self.fetch_with_retry(
                    f"{self.api_base_url}/getassociatedspecies",
                    params={"phenotype": disease_name}
                )

                for species in response.get("associated_species", []):
                    yield {
                        "disease_name": disease_name,
                        "disease_meshid": response.get("meshid"),
                        "taxon_name": species.get("species_name"),
                        "ncbi_taxid": species.get("ncbi_taxid"),
                        "direction": species.get("direction", "altered"),
                        "n_samples_increased": species.get("nr_increased", 0),
                        "n_samples_decreased": species.get("nr_decreased", 0),
                        "n_samples_total": species.get("nr_total", 0),
                    }
            except Exception as e:
                logger.warning(f"Failed to fetch GMrepo data for {disease_name}: {e}")
                continue

    def transform(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Transform GMrepo record to graph format."""
        taxon_name = record.get("taxon_name")
        disease_name = record.get("disease_name")

        if not taxon_name or not disease_name:
            return None

        ncbi_taxid = record.get("ncbi_taxid")
        taxon_id = f"NCBITaxon:{ncbi_taxid}" if ncbi_taxid else f"gmrepo:{taxon_name.replace(' ', '_').lower()}"

        meshid = record.get("disease_meshid")
        disease_id = f"MESH:{meshid}" if meshid else f"gmrepo:{disease_name.replace(' ', '_').lower()}"

        # Normalize disease name for consistent entity resolution across data sources
        disease_name_normalized = normalize_disease_name(disease_name)

        # Determine direction from sample counts
        n_increased = record.get("n_samples_increased", 0)
        n_decreased = record.get("n_samples_decreased", 0)

        if n_increased > n_decreased * 1.5:
            direction = "enriched"
        elif n_decreased > n_increased * 1.5:
            direction = "depleted"
        else:
            direction = "altered"

        return {
            "taxon_id": taxon_id,
            "taxon_name": taxon_name,
            "ncbi_taxid": ncbi_taxid,
            "disease_id": disease_id,
            "disease_name": disease_name,
            "disease_name_normalized": disease_name_normalized,
            "mesh_id": meshid,
            "direction": direction,
            "n_samples_increased": n_increased,
            "n_samples_decreased": n_decreased,
            "n_samples_total": record.get("n_samples_total", 0),
            "evidence_level": "curated",
            "source": "gmrepo",
            "organization_id": self.organization_id,
        }

    def load_batch(self, batch: List[Dict[str, Any]]) -> Dict[str, int]:
        """Load GMrepo batch - similar to DisbiomeLoader."""
        # Reuse DisbiomeLoader logic
        nodes_created = 0
        rels_created = 0

        # Taxa
        taxa = {}
        for r in batch:
            if r["taxon_id"] not in taxa:
                taxa[r["taxon_id"]] = {
                    "taxon_id": r["taxon_id"],
                    "name": r["taxon_name"],
                    "ncbi_tax_id": r.get("ncbi_taxid"),
                    "rank": "species",
                    "organization_id": self.organization_id,
                }

        query = """
            UNWIND $taxa AS t
            MERGE (taxon:Taxon {taxon_id: t.taxon_id})
            ON CREATE SET taxon += t, taxon.created_at = datetime()
            RETURN count(taxon) AS count
        """
        result = self.execute_cypher(query, {"taxa": list(taxa.values())})
        nodes_created += result[0]["count"] if result else 0

        # Diseases - use normalized name for consistent entity resolution
        diseases = {}
        for r in batch:
            norm_name = r.get("disease_name_normalized", r["disease_name"].lower())
            if norm_name and norm_name not in diseases:
                diseases[norm_name] = {
                    "name_normalized": norm_name,
                    "name": r["disease_name"],
                    "disease_id": r["disease_id"],
                    "mesh_id": r.get("mesh_id"),
                    "microbiome_associated": True,
                    "organization_id": self.organization_id,
                    "source": "gmrepo",
                }

        query = """
            UNWIND $diseases AS d
            MERGE (disease:Disease {name_normalized: d.name_normalized})
            ON CREATE SET
                disease.name = d.name,
                disease.disease_id = d.disease_id,
                disease.mesh_id = d.mesh_id,
                disease.microbiome_associated = d.microbiome_associated,
                disease.organization_id = d.organization_id,
                disease.sources = [d.source],
                disease.created_at = datetime()
            ON MATCH SET
                disease.mesh_id = COALESCE(disease.mesh_id, d.mesh_id),
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

        # Associations - match on normalized disease name
        query = """
            UNWIND $batch AS a
            MATCH (taxon:Taxon {taxon_id: a.taxon_id})
            MATCH (disease:Disease {name_normalized: a.disease_name_normalized})
            MERGE (taxon)-[r:ASSOCIATED_WITH_DISEASE]->(disease)
            ON CREATE SET
                r.direction = a.direction,
                r.n_samples_increased = a.n_samples_increased,
                r.n_samples_decreased = a.n_samples_decreased,
                r.n_samples_total = a.n_samples_total,
                r.evidence_level = a.evidence_level,
                r.sources = [a.source],
                r.created_at = datetime()
            ON MATCH SET
                r.n_samples_increased = a.n_samples_increased,
                r.n_samples_decreased = a.n_samples_decreased,
                r.n_samples_total = a.n_samples_total,
                r.sources = CASE WHEN NOT a.source IN r.sources THEN r.sources + a.source ELSE r.sources END,
                r.updated_at = datetime()
            RETURN count(r) AS count
        """
        result = self.execute_cypher(query, {"batch": batch})
        rels_created += result[0]["count"] if result else 0

        return {"nodes_created": nodes_created, "relationships_created": rels_created}


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    from neo4j import GraphDatabase

    load_dotenv()

    NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
    NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    try:
        # Test GMrepo loader with a few diseases
        loader = GMrepoLoader(
            driver=driver,
            organization_id="default",
        )

        # Load data for select diseases
        test_diseases = [
            "inflammatory bowel disease",
            "type 2 diabetes mellitus",
            "colorectal cancer",
            "obesity"
        ]

        stats = loader.run(diseases=test_diseases)
        print(f"Loaded GMrepo data: {stats.to_dict()}")

    finally:
        driver.close()
