"""
GMrepo Data Loader

Loads human gut metagenome disease-microbiome association data from the GMrepo API
into Neo4j. GMrepo aggregates curated gut microbiota composition data from published
metagenomic sequencing projects.

Data source: https://gmrepo.humangut.info/api
"""

from typing import Optional, Iterator, Dict, Any, List
from .base_loader import APIBasedLoader, normalize_disease_name, generate_disease_id
import logging

logger = logging.getLogger(__name__)

# Map raw direction strings to canonical direction values
DIRECTION_MAP = {
    "increased": "enriched",
    "enriched": "enriched",
    "decreased": "depleted",
    "depleted": "depleted",
    "altered": "altered",
}


class GMrepoLoader(APIBasedLoader):
    """
    Load disease-microbiome associations from the GMrepo API.

    GMrepo is a curated database containing gut microbiota composition
    data collected from published metagenomic sequencing projects.

    API: https://gmrepo.humangut.info/api
    """

    # MeSH IDs that are not diseases and should not become Disease nodes.
    # D006262 = "Health" (GMrepo's control group).
    SKIP_MESH_IDS = frozenset({"D006262"})

    @property
    def source_name(self) -> str:
        return "GMrepo"

    def __init__(
        self,
        driver,
        organization_id: str,
        batch_size: int = 500,
        database: str = "neo4j",
        max_phenotypes: int = None,
    ):
        """
        Initialize the GMrepo loader.

        Args:
            driver: Neo4j driver
            organization_id: Organization ID for multi-tenant isolation
            batch_size: Records per batch
            database: Neo4j database name
            max_phenotypes: Maximum number of phenotypes to process (None for all)
        """
        super().__init__(
            driver,
            organization_id,
            api_base_url="https://gmrepo.humangut.info/api",
            batch_size=batch_size,
            database=database,
            rate_limit_delay=0.5,
        )
        self.max_phenotypes = max_phenotypes

    def extract(self, **kwargs) -> Iterator[Dict[str, Any]]:
        """
        Extract disease-microbiome associations from GMrepo API.

        Fetches the full list of phenotypes, then for each phenotype retrieves
        the associated species and genera with their mean relative abundances.

        The GMrepo backend is a Django app that only accepts POST requests and
        requires a trailing slash on every endpoint (``APPEND_SLASH`` is on, and
        it cannot redirect a POST body to the slash URL). It keys taxon
        associations by the phenotype's MeSH ID, not its display name.

        Yields:
            Per-taxon records from GMrepo with the phenotype name attached
        """
        # Fetch all phenotypes (POST + trailing slash; GET returns code 1002)
        phenotypes_url = f"{self.api_base_url}/get_all_phenotypes/"
        logger.info(f"Fetching phenotypes from {phenotypes_url}")
        phenotypes_response = self.fetch_with_retry(
            phenotypes_url, method="POST", json={}
        )

        # Handle dict wrapping (phenotypes or data keys) or plain list
        if isinstance(phenotypes_response, list):
            phenotypes = phenotypes_response
        elif isinstance(phenotypes_response, dict):
            phenotypes = (
                phenotypes_response.get("phenotypes")
                or phenotypes_response.get("data")
                or []
            )
        else:
            logger.warning(f"Unexpected phenotypes response type: {type(phenotypes_response)}")
            return

        # Respect max_phenotypes limit
        if self.max_phenotypes is not None:
            phenotypes = phenotypes[:self.max_phenotypes]

        for phenotype in phenotypes:
            # The phenotype list carries the MeSH ID in `disease` and the
            # human-readable name in `term`. Tolerate older/alternate keys.
            if isinstance(phenotype, dict):
                mesh_id = phenotype.get("disease") or phenotype.get("mesh_id")
                phenotype_name = (
                    phenotype.get("term")
                    or phenotype.get("phenotype")
                    or phenotype.get("name", "")
                )
            else:
                mesh_id = None
                phenotype_name = str(phenotype)

            if not mesh_id or not phenotype_name:
                continue

            # GMrepo's "Health" phenotype is the control group, not a disease.
            if mesh_id in self.SKIP_MESH_IDS:
                continue

            logger.info(f"Fetching GMrepo taxa for phenotype: {phenotype_name} ({mesh_id})")
            try:
                taxa_url = (
                    f"{self.api_base_url}/getAssociatedMicrobiotaDatasetsByPhenotypeMeshID/"
                )
                response = self.fetch_with_retry(
                    taxa_url, method="POST", json={"mesh_id": mesh_id}
                )

                if not isinstance(response, dict):
                    logger.warning(f"Unexpected taxa response for {mesh_id}: {type(response)}")
                    continue

                # Combine species- and genus-level associations.
                taxa_records = list(response.get("associated_species") or [])
                taxa_records += list(response.get("associated_genus") or [])

                for taxon_record in taxa_records:
                    taxon_record["phenotype"] = phenotype_name
                    taxon_record["mesh_id"] = mesh_id
                    yield taxon_record

            except Exception as e:
                logger.warning(f"Failed to fetch taxa for {phenotype_name} ({mesh_id}): {e}")
                continue

    def transform(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Transform a GMrepo taxon-phenotype record to graph format.

        Args:
            record: Raw record from GMrepo API with phenotype name attached

        Returns:
            Transformed record ready for Neo4j loading, or None to skip
        """
        # The live API names the taxon `scientific_name` and abundance fields
        # `abus_mean`/`abus_sd`/`samples`; fall back to the legacy field names.
        organism_name = record.get("scientific_name") or record.get("organism_name")
        ncbi_taxon_id = record.get("ncbi_taxon_id")
        phenotype = record.get("phenotype")

        if not organism_name and not ncbi_taxon_id:
            return None

        if not phenotype:
            return None

        # Generate taxon ID
        if ncbi_taxon_id:
            taxon_id = f"NCBITaxon:{ncbi_taxon_id}"
        else:
            taxon_id = f"gmrepo:{organism_name.replace(' ', '_').lower()}"

        # Normalize disease name and generate disease ID
        disease_name_normalized = normalize_disease_name(phenotype)
        disease_id = generate_disease_id(phenotype)

        # Map direction. The datasets endpoint reports abundance but not an
        # enriched/depleted call, so most records fall through to "altered".
        raw_direction = str(record.get("direction", "altered")).lower().strip()
        direction = DIRECTION_MAP.get(raw_direction, "altered")

        # Taxon rank comes from the source ("species"/"genus"); default species.
        rank = record.get("taxon_rank_level") or "species"

        return {
            "taxon_id": taxon_id,
            "taxon_name": organism_name or f"taxon_{ncbi_taxon_id}",
            "ncbi_taxon_id": ncbi_taxon_id,
            "rank": rank,
            "disease_id": disease_id,
            "disease_name": phenotype,
            "disease_name_normalized": disease_name_normalized,
            "direction": direction,
            "abundance_mean": record.get("abus_mean", record.get("abundance_mean")),
            "abundance_std": record.get("abus_sd", record.get("abundance_std")),
            "samples_count": record.get("samples", record.get("samples_count")),
            "source": "gmrepo",
            "organization_id": self.organization_id,
        }

    def load_batch(self, batch: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Load a batch of GMrepo records into Neo4j.

        Creates:
        - Taxon nodes (MERGE by taxon_id)
        - Disease nodes (MERGE by name_normalized)
        - ASSOCIATED_WITH_DISEASE relationships with abundance metadata
        """
        if not batch:
            return {"nodes_created": 0, "relationships_created": 0}

        nodes_created = 0
        rels_created = 0

        # --- Taxon nodes ---
        taxa = {}
        for r in batch:
            if r["taxon_id"] not in taxa:
                taxa[r["taxon_id"]] = {
                    "taxon_id": r["taxon_id"],
                    "name": r["taxon_name"],
                    "ncbi_tax_id": r.get("ncbi_taxon_id"),
                    "rank": r.get("rank", "species"),
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
        nodes_created += result[0]["count"] if result else 0

        # --- Disease nodes ---
        diseases = {}
        for r in batch:
            norm_name = r["disease_name_normalized"]
            if norm_name and norm_name not in diseases:
                diseases[norm_name] = {
                    "name_normalized": norm_name,
                    "name": r["disease_name"],
                    "disease_id": r["disease_id"],
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

        # --- Associations ---
        query = """
            UNWIND $batch AS a
            MATCH (taxon:Taxon {taxon_id: a.taxon_id})
            MATCH (disease:Disease {name_normalized: a.disease_name_normalized})
            MERGE (taxon)-[r:ASSOCIATED_WITH_DISEASE]->(disease)
            ON CREATE SET
                r.direction = a.direction,
                r.abundance_mean = a.abundance_mean,
                r.abundance_std = a.abundance_std,
                r.samples_count = a.samples_count,
                r.sources = [a.source],
                r.created_at = datetime()
            ON MATCH SET
                r.abundance_mean = COALESCE(a.abundance_mean, r.abundance_mean),
                r.abundance_std = COALESCE(a.abundance_std, r.abundance_std),
                r.samples_count = COALESCE(a.samples_count, r.samples_count),
                r.sources = CASE
                    WHEN NOT a.source IN r.sources THEN r.sources + a.source
                    ELSE r.sources
                END,
                r.updated_at = datetime()
            RETURN count(r) AS count
        """
        result = self.execute_cypher(query, {"batch": batch})
        rels_created += result[0]["count"] if result else 0

        return {"nodes_created": nodes_created, "relationships_created": rels_created}
