"""
ChEMBL Data Loader

Loads drug-target interaction data from ChEMBL database into Neo4j.
ChEMBL is an open-access database of bioactive molecules with drug-like properties.

License: CC BY-SA 3.0 - Commercial use allowed with attribution.

Data source: https://www.ebi.ac.uk/chembl/
API docs: https://www.ebi.ac.uk/chembl/api/data/docs
"""

from typing import Optional, Iterator, Dict, Any, List
from .base_loader import APIBasedLoader
import logging
import time

logger = logging.getLogger(__name__)


class ChEMBLLoader(APIBasedLoader):
    """
    Load drug-target interactions from ChEMBL API.

    Creates:
    - Drug nodes (approved drugs from ChEMBL)
    - Target nodes (protein targets)
    - TARGETS relationships (Drug -> Target)

    Uses ChEMBL REST API to fetch:
    - Approved drugs (max_phase=4)
    - Drug mechanisms (mechanism of action)
    - Target information
    """

    @property
    def source_name(self) -> str:
        return "ChEMBL"

    def __init__(
        self,
        driver,
        organization_id: str,
        batch_size: int = 500,
        database: str = "neo4j",
        max_drugs: Optional[int] = None,
        include_clinical_candidates: bool = False
    ):
        """
        Initialize the ChEMBL loader.

        Args:
            driver: Neo4j driver
            organization_id: Organization ID for multi-tenant isolation
            batch_size: Records per batch
            database: Neo4j database name
            max_drugs: Maximum number of drugs to load (None = all)
            include_clinical_candidates: Include phase 3 drugs (default: only approved)
        """
        super().__init__(
            driver,
            organization_id,
            api_base_url="https://www.ebi.ac.uk/chembl/api/data",
            batch_size=batch_size,
            database=database,
            rate_limit_delay=0.2  # ChEMBL is fast, but be respectful
        )
        self.max_drugs = max_drugs
        self.include_clinical_candidates = include_clinical_candidates
        self._target_cache: Dict[str, Dict] = {}
        self._mechanism_cache: Dict[str, List[Dict]] = {}

    def extract(self, **kwargs) -> Iterator[Dict[str, Any]]:
        """
        Extract approved drugs and their targets from ChEMBL.

        Yields:
            Drug-target interaction records
        """
        # Determine max_phase filter
        if self.include_clinical_candidates:
            min_phase = 3  # Phase 3 and 4
        else:
            min_phase = 4  # Only approved (phase 4)

        logger.info(f"Fetching approved drugs from ChEMBL (min_phase={min_phase})...")

        # Fetch approved drugs
        drugs = self._fetch_approved_drugs(min_phase)

        if self.max_drugs:
            drugs = drugs[:self.max_drugs]

        logger.info(f"Found {len(drugs)} drugs. Fetching mechanisms and targets...")

        for i, drug in enumerate(drugs):
            chembl_id = drug.get("molecule_chembl_id")
            if not chembl_id:
                continue

            # Fetch mechanisms of action for this drug
            mechanisms = self._fetch_drug_mechanisms(chembl_id)

            if not mechanisms:
                # Drug without known mechanism - still yield the drug
                yield {
                    "drug": drug,
                    "mechanism": None,
                    "target": None
                }
            else:
                for mechanism in mechanisms:
                    target_chembl_id = mechanism.get("target_chembl_id")
                    target = None

                    if target_chembl_id:
                        target = self._fetch_target(target_chembl_id)

                    yield {
                        "drug": drug,
                        "mechanism": mechanism,
                        "target": target
                    }

            if (i + 1) % 100 == 0:
                logger.info(f"Processed {i + 1}/{len(drugs)} drugs...")

    def _fetch_approved_drugs(self, min_phase: int = 4) -> List[Dict]:
        """Fetch all approved drugs from ChEMBL."""
        drugs = []
        offset = 0
        limit = 1000

        while True:
            url = f"{self.api_base_url}/molecule.json"
            params = {
                "max_phase__gte": min_phase,
                "limit": limit,
                "offset": offset
            }

            try:
                response = self.fetch_with_retry(url, params=params)
                molecules = response.get("molecules", [])

                if not molecules:
                    break

                drugs.extend(molecules)
                offset += limit

                if len(molecules) < limit:
                    break

                # Safety limit
                if len(drugs) >= 50000:
                    logger.warning("Reached safety limit of 50000 drugs")
                    break

            except Exception as e:
                logger.exception("Error fetching drugs: %s", e)
                break

        return drugs

    def _fetch_drug_mechanisms(self, chembl_id: str) -> List[Dict]:
        """Fetch mechanisms of action for a drug."""
        if chembl_id in self._mechanism_cache:
            return self._mechanism_cache[chembl_id]

        url = f"{self.api_base_url}/mechanism.json"
        params = {"molecule_chembl_id": chembl_id}

        try:
            response = self.fetch_with_retry(url, params=params)
            mechanisms = response.get("mechanisms", [])
            self._mechanism_cache[chembl_id] = mechanisms
            return mechanisms
        except Exception as e:
            logger.warning(f"Error fetching mechanisms for {chembl_id}: {e}")
            return []

    def _fetch_target(self, target_chembl_id: str) -> Optional[Dict]:
        """Fetch target details."""
        if target_chembl_id in self._target_cache:
            return self._target_cache[target_chembl_id]

        url = f"{self.api_base_url}/target/{target_chembl_id}.json"

        try:
            target = self.fetch_with_retry(url)
            self._target_cache[target_chembl_id] = target
            return target
        except Exception as e:
            logger.warning(f"Error fetching target {target_chembl_id}: {e}")
            return None

    def transform(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Transform ChEMBL record to graph format.

        Args:
            record: Raw record with drug, mechanism, target

        Returns:
            Transformed record for Neo4j
        """
        drug = record.get("drug", {})
        mechanism = record.get("mechanism") or {}
        target = record.get("target") or {}

        if not drug:
            return None

        # Extract drug info
        drug_id = drug.get("molecule_chembl_id")
        if not drug_id:
            return None

        drug_props = drug.get("molecule_properties") or {}
        drug_structures = drug.get("molecule_structures") or {}

        # Determine drug type
        molecule_type = drug.get("molecule_type", "Unknown")

        result = {
            # Drug info
            "drug_id": f"CHEMBL:{drug_id}",
            "drug_chembl_id": drug_id,
            "drug_name": drug.get("pref_name") or drug_id,
            "drug_type": molecule_type,
            "max_phase": drug.get("max_phase"),
            "first_approval": drug.get("first_approval"),
            "oral": drug.get("oral"),
            "parenteral": drug.get("parenteral"),
            "topical": drug.get("topical"),
            "black_box_warning": drug.get("black_box_warning"),
            "indication_class": drug.get("indication_class"),

            # Molecular properties
            "molecular_weight": drug_props.get("mw_freebase"),
            "alogp": drug_props.get("alogp"),
            "hba": drug_props.get("hba"),
            "hbd": drug_props.get("hbd"),
            "psa": drug_props.get("psa"),
            "rtb": drug_props.get("rtb"),
            "ro3_pass": drug_props.get("ro3_pass"),
            "num_ro5_violations": drug_props.get("num_ro5_violations"),

            # Structures
            "smiles": drug_structures.get("canonical_smiles"),
            "inchi_key": drug_structures.get("standard_inchi_key"),

            # Organization
            "organization_id": self.organization_id,
        }

        # Add mechanism info if available
        if mechanism:
            result["mechanism_action_type"] = mechanism.get("action_type")
            result["mechanism_description"] = mechanism.get("mechanism_of_action")
            result["direct_interaction"] = mechanism.get("direct_interaction")

        # Add target info if available
        if target:
            target_id = target.get("target_chembl_id")
            result["target_id"] = f"CHEMBL:{target_id}" if target_id else None
            result["target_chembl_id"] = target_id
            result["target_name"] = target.get("pref_name")
            result["target_type"] = target.get("target_type")
            result["target_organism"] = target.get("organism")

            # Get UniProt IDs from target components
            target_components = target.get("target_components", [])
            uniprot_ids = []
            for comp in target_components:
                accession = comp.get("accession")
                if accession:
                    uniprot_ids.append(accession)
            result["target_uniprot_ids"] = uniprot_ids

        return result

    def load_batch(self, batch: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Load a batch of ChEMBL records into Neo4j.

        Creates:
        - Drug nodes
        - Target nodes (Protein)
        - TARGETS relationships
        """
        if not batch:
            return {"nodes_created": 0, "relationships_created": 0}

        nodes_created = 0
        rels_created = 0

        # Load drugs
        nodes_created += self._load_drugs(batch)

        # Load targets
        nodes_created += self._load_targets(batch)

        # Load drug-target relationships
        rels_created += self._load_drug_target_relationships(batch)

        return {
            "nodes_created": nodes_created,
            "relationships_created": rels_created
        }

    def _load_drugs(self, batch: List[Dict[str, Any]]) -> int:
        """Load Drug nodes from batch."""
        # Deduplicate drugs
        drugs = {}
        for record in batch:
            drug_id = record.get("drug_id")
            if drug_id and drug_id not in drugs:
                drugs[drug_id] = {
                    "drug_id": drug_id,
                    "chembl_id": record.get("drug_chembl_id"),
                    "name": record.get("drug_name"),
                    "drug_type": record.get("drug_type"),
                    "max_phase": record.get("max_phase"),
                    "first_approval": record.get("first_approval"),
                    "oral": record.get("oral"),
                    "parenteral": record.get("parenteral"),
                    "topical": record.get("topical"),
                    "black_box_warning": record.get("black_box_warning"),
                    "indication_class": record.get("indication_class"),
                    "molecular_weight": record.get("molecular_weight"),
                    "smiles": record.get("smiles"),
                    "inchi_key": record.get("inchi_key"),
                    "organization_id": self.organization_id,
                }

        if not drugs:
            return 0

        query = """
            UNWIND $drugs AS d
            MERGE (drug:Drug {drug_id: d.drug_id})
            ON CREATE SET
                drug.chembl_id = d.chembl_id,
                drug.name = d.name,
                drug.drug_type = d.drug_type,
                drug.max_phase = d.max_phase,
                drug.first_approval = d.first_approval,
                drug.oral = d.oral,
                drug.parenteral = d.parenteral,
                drug.topical = d.topical,
                drug.black_box_warning = d.black_box_warning,
                drug.indication_class = d.indication_class,
                drug.molecular_weight = d.molecular_weight,
                drug.smiles = d.smiles,
                drug.inchi_key = d.inchi_key,
                drug.organization_id = d.organization_id,
                drug.source = 'chembl',
                drug.sources = ['chembl'],
                drug.created_at = datetime()
            RETURN count(drug) AS count
        """

        result = self.execute_cypher(query, {"drugs": list(drugs.values())})
        return result[0]["count"] if result else 0

    def _load_targets(self, batch: List[Dict[str, Any]]) -> int:
        """Load Target nodes (as Protein) from batch."""
        # Deduplicate targets
        targets = {}
        for record in batch:
            target_id = record.get("target_id")
            if target_id and target_id not in targets:
                targets[target_id] = {
                    "protein_id": target_id,
                    "chembl_id": record.get("target_chembl_id"),
                    "name": record.get("target_name"),
                    "target_type": record.get("target_type"),
                    "organism": record.get("target_organism"),
                    "uniprot_ids": record.get("target_uniprot_ids", []),
                    "organization_id": self.organization_id,
                }

        if not targets:
            return 0

        query = """
            UNWIND $targets AS t
            MERGE (protein:Protein {protein_id: t.protein_id})
            ON CREATE SET
                protein.chembl_id = t.chembl_id,
                protein.name = t.name,
                protein.target_type = t.target_type,
                protein.organism = t.organism,
                protein.uniprot_ids = t.uniprot_ids,
                protein.organization_id = t.organization_id,
                protein.source = 'chembl',
                protein.sources = ['chembl'],
                protein.created_at = datetime()
            ON MATCH SET
                protein.chembl_id = COALESCE(protein.chembl_id, t.chembl_id),
                protein.name = COALESCE(protein.name, t.name),
                protein.target_type = COALESCE(protein.target_type, t.target_type),
                protein.organism = COALESCE(protein.organism, t.organism),
                protein.updated_at = datetime()
            RETURN count(protein) AS count
        """

        result = self.execute_cypher(query, {"targets": list(targets.values())})
        return result[0]["count"] if result else 0

    def _load_drug_target_relationships(self, batch: List[Dict[str, Any]]) -> int:
        """Load Drug-Target relationships."""
        relationships = []
        seen = set()

        for record in batch:
            drug_id = record.get("drug_id")
            target_id = record.get("target_id")

            if drug_id and target_id:
                key = (drug_id, target_id)
                if key not in seen:
                    seen.add(key)
                    relationships.append({
                        "drug_id": drug_id,
                        "target_id": target_id,
                        "action_type": record.get("mechanism_action_type"),
                        "mechanism_description": record.get("mechanism_description"),
                        "direct_interaction": record.get("direct_interaction"),
                        "source": "chembl"
                    })

        if not relationships:
            return 0

        query = """
            UNWIND $relationships AS r
            MATCH (drug:Drug {drug_id: r.drug_id})
            MATCH (target:Protein {protein_id: r.target_id})
            MERGE (drug)-[rel:TARGETS]->(target)
            ON CREATE SET
                rel.action_type = r.action_type,
                rel.mechanism_description = r.mechanism_description,
                rel.direct_interaction = r.direct_interaction,
                rel.source = r.source,
                rel.created_at = datetime()
            RETURN count(rel) AS count
        """

        result = self.execute_cypher(query, {"relationships": relationships})
        return result[0]["count"] if result else 0


# Convenience function for microbiome-related drugs

def load_microbiome_related_drugs(driver, organization_id: str = "default") -> Dict[str, Any]:
    """
    Load drugs relevant to microbiome research from ChEMBL.

    Includes drugs for:
    - GI conditions (IBD, IBS, C. diff, etc.)
    - Metabolic disorders (diabetes, obesity, NAFLD)
    - Antibiotics (affecting gut microbiome)
    - Probiotics/prebiotics-related
    - Immunomodulators
    - Bile acid modulators

    Uses ChEMBL drug search by indication class.
    """
    # Microbiome-relevant drug categories to search
    search_terms = [
        # GI conditions
        "inflammatory bowel disease",
        "ulcerative colitis",
        "crohn",
        "irritable bowel syndrome",
        "clostridium difficile",
        "diarrhea",
        "constipation",

        # Metabolic
        "type 2 diabetes",
        "obesity",
        "metabolic syndrome",
        "non-alcoholic fatty liver",

        # Antibiotics (gut-affecting)
        "antibiotic",
        "antimicrobial",
        "rifaximin",
        "metronidazole",
        "vancomycin",
        "fidaxomicin",

        # Bile acids
        "bile acid",
        "ursodeoxycholic",
        "obeticholic",

        # Immunomodulators
        "immunosuppressant",
        "anti-inflammatory",

        # Probiotics-related
        "lactobacillus",
        "bifidobacterium",
    ]

    logger.info("Searching ChEMBL for microbiome-related drugs...")

    # Use ChEMBL API to search for drugs by indication
    import requests

    all_chembl_ids = set()
    api_base = "https://www.ebi.ac.uk/chembl/api/data"

    for term in search_terms:
        try:
            # Search drug indications
            url = f"{api_base}/drug_indication.json"
            params = {
                "mesh_heading__icontains": term,
                "limit": 100
            }
            time.sleep(0.3)
            response = requests.get(url, params=params, timeout=30)

            if response.status_code == 200:
                data = response.json()
                indications = data.get("drug_indications", [])
                for ind in indications:
                    chembl_id = ind.get("molecule_chembl_id")
                    if chembl_id:
                        all_chembl_ids.add(chembl_id)
                logger.info(f"  '{term}' -> {len(indications)} indications found")

        except Exception as e:
            logger.warning(f"Error searching '{term}': {e}")

    logger.info(f"Found {len(all_chembl_ids)} unique drugs for microbiome-related conditions")

    if not all_chembl_ids:
        return {"nodes_created": 0, "relationships_created": 0, "duration_seconds": 0}

    # Now load these specific drugs using the loader
    # We'll fetch the drug details and mechanisms for these specific ChEMBL IDs
    loader = ChEMBLLoader(
        driver=driver,
        organization_id=organization_id,
        max_drugs=len(all_chembl_ids)  # Will be filtered by our list
    )

    # Override extract to only fetch our specific drugs
    original_extract = loader.extract

    def filtered_extract(**kwargs):
        """Only yield drugs from our curated list."""
        for record in original_extract(**kwargs):
            drug = record.get("drug", {})
            chembl_id = drug.get("molecule_chembl_id")
            if chembl_id in all_chembl_ids:
                yield record

    loader.extract = filtered_extract

    stats = loader.run()
    return stats.to_dict()


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    from neo4j import GraphDatabase

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
    NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    try:
        loader = ChEMBLLoader(
            driver=driver,
            organization_id="default",
            max_drugs=100  # Test with 100 drugs first
        )

        stats = loader.run()
        print("\nChEMBL Loading Complete!")
        print(f"  Nodes created: {stats.nodes_created}")
        print(f"  Relationships created: {stats.relationships_created}")
        print(f"  Duration: {stats.duration_seconds:.1f} seconds")
    finally:
        driver.close()
