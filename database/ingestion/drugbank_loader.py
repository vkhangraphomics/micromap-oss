"""
DrugBank Data Loader

Loads drug-target and drug-metabolism data from DrugBank XML into Neo4j.
Focuses on drug-microbiome interactions relevant to pharmacomicrobiomics.

Data source: https://go.drugbank.com/releases/latest
License: CC BY-NC 4.0 (Academic use only)
"""

from typing import Optional, Iterator, Dict, Any, List
from .base_loader import FileBasedLoader
import xml.etree.ElementTree as ET
import gzip
import logging

logger = logging.getLogger(__name__)

# DrugBank XML namespace
DB_NS = "{http://www.drugbank.ca}"


class DrugBankLoader(FileBasedLoader):
    """
    Load DrugBank data into Neo4j.

    Creates:
    - Drug nodes with comprehensive properties
    - Protein nodes for targets, enzymes, transporters, carriers
    - Drug-target relationships (TARGETS, METABOLIZED_BY, TRANSPORTED_BY)

    Special focus on:
    - Oral bioavailability (relevant for gut microbiome exposure)
    - CYP enzyme metabolism
    - P-glycoprotein transport
    - Known microbiome interactions
    """

    @property
    def source_name(self) -> str:
        return "DrugBank"

    def __init__(
        self,
        driver,
        organization_id: str,
        file_path: str,
        batch_size: int = 100,
        database: str = "neo4j",
        include_experimental: bool = False
    ):
        """
        Initialize the DrugBank loader.

        Args:
            driver: Neo4j driver
            organization_id: Organization ID
            file_path: Path to drugbank_all_full_database.xml.gz
            batch_size: Records per batch (smaller due to XML complexity)
            database: Neo4j database name
            include_experimental: Include experimental/investigational drugs
        """
        super().__init__(driver, organization_id, file_path, batch_size, database)
        self.include_experimental = include_experimental

    def extract(self, **kwargs) -> Iterator[Dict[str, Any]]:
        """
        Extract drug data from DrugBank XML.

        Uses iterparse for memory-efficient processing of large XML file.
        """
        logger.info(f"Loading DrugBank data from {self.file_path}")

        # Open file (handle gzip). Both gzip.open and the builtin open are
        # context managers, so a single `with` covers both branches.
        opener = gzip.open if self.file_path.endswith(".gz") else open
        with opener(self.file_path, "rb") as file_handle:
            # Use iterparse for memory efficiency
            context = ET.iterparse(file_handle, events=("end",))

            for event, elem in context:
                if elem.tag == f"{DB_NS}drug":
                    drug_data = self._parse_drug_element(elem)

                    if drug_data:
                        # Filter by drug groups if needed
                        groups = drug_data.get("groups", [])
                        if not self.include_experimental:
                            if "approved" not in groups and "nutraceutical" not in groups:
                                elem.clear()
                                continue

                        yield drug_data

                    # Clear element to free memory
                    elem.clear()

    def _parse_drug_element(self, drug_elem) -> Optional[Dict[str, Any]]:
        """Parse a single drug element from XML."""
        try:
            # Basic info
            drugbank_id = None
            for db_id in drug_elem.findall(f"{DB_NS}drugbank-id"):
                if db_id.get("primary") == "true":
                    drugbank_id = db_id.text
                    break

            if not drugbank_id:
                return None

            name = self._get_text(drug_elem, f"{DB_NS}name")
            description = self._get_text(drug_elem, f"{DB_NS}description")
            drug_type = drug_elem.get("type", "small molecule")

            # Groups (approved, investigational, etc.)
            groups = [g.text for g in drug_elem.findall(f"{DB_NS}groups/{DB_NS}group")]

            # Categories
            categories = [c.find(f"{DB_NS}category").text
                         for c in drug_elem.findall(f"{DB_NS}categories/{DB_NS}category")
                         if c.find(f"{DB_NS}category") is not None]

            # Chemical properties
            cas_number = self._get_text(drug_elem, f"{DB_NS}cas-number")

            # Get calculated properties
            calc_props = {}
            for prop in drug_elem.findall(f"{DB_NS}calculated-properties/{DB_NS}property"):
                kind = self._get_text(prop, f"{DB_NS}kind")
                value = self._get_text(prop, f"{DB_NS}value")
                if kind and value:
                    calc_props[kind] = value

            # Get experimental properties
            exp_props = {}
            for prop in drug_elem.findall(f"{DB_NS}experimental-properties/{DB_NS}property"):
                kind = self._get_text(prop, f"{DB_NS}kind")
                value = self._get_text(prop, f"{DB_NS}value")
                if kind and value:
                    exp_props[kind] = value

            # External identifiers
            external_ids = {}
            for ext_id in drug_elem.findall(f"{DB_NS}external-identifiers/{DB_NS}external-identifier"):
                resource = self._get_text(ext_id, f"{DB_NS}resource")
                identifier = self._get_text(ext_id, f"{DB_NS}identifier")
                if resource and identifier:
                    external_ids[resource] = identifier

            # ATC codes
            atc_codes = [atc.get("code") for atc in drug_elem.findall(f"{DB_NS}atc-codes/{DB_NS}atc-code")]

            # Pharmacology text fields
            mechanism_of_action = self._get_text(drug_elem, f"{DB_NS}mechanism-of-action")
            pharmacodynamics = self._get_text(drug_elem, f"{DB_NS}pharmacodynamics")
            absorption = self._get_text(drug_elem, f"{DB_NS}absorption")
            metabolism = self._get_text(drug_elem, f"{DB_NS}metabolism")

            # Routes of administration
            routes = list(set([
                r.text for r in drug_elem.findall(f"{DB_NS}dosages/{DB_NS}dosage/{DB_NS}route")
                if r.text
            ]))

            # Targets, enzymes, transporters, carriers
            targets = self._parse_bio_entities(drug_elem, f"{DB_NS}targets/{DB_NS}target", "target")
            enzymes = self._parse_bio_entities(drug_elem, f"{DB_NS}enzymes/{DB_NS}enzyme", "enzyme")
            transporters = self._parse_bio_entities(drug_elem, f"{DB_NS}transporters/{DB_NS}transporter", "transporter")
            carriers = self._parse_bio_entities(drug_elem, f"{DB_NS}carriers/{DB_NS}carrier", "carrier")

            # Food interactions (relevant for gut microbiome)
            food_interactions = [fi.text for fi in drug_elem.findall(f"{DB_NS}food-interactions/{DB_NS}food-interaction")]

            return {
                "drugbank_id": drugbank_id,
                "name": name,
                "description": description,
                "drug_type": drug_type,
                "groups": groups,
                "categories": categories,
                "cas_number": cas_number,
                "smiles": calc_props.get("SMILES"),
                "inchi": calc_props.get("InChI"),
                "inchi_key": calc_props.get("InChIKey"),
                "molecular_weight": self._safe_float(calc_props.get("Molecular Weight")),
                "molecular_formula": calc_props.get("Molecular Formula"),
                "logp": self._safe_float(calc_props.get("logP")),
                "water_solubility": exp_props.get("Water Solubility"),
                "mechanism_of_action": mechanism_of_action,
                "pharmacodynamics": pharmacodynamics,
                "absorption": absorption,
                "metabolism": metabolism,
                "routes_of_administration": routes,
                "atc_codes": atc_codes,
                "external_ids": external_ids,
                "targets": targets,
                "enzymes": enzymes,
                "transporters": transporters,
                "carriers": carriers,
                "food_interactions": food_interactions,
            }

        except Exception as e:
            logger.warning(f"Failed to parse drug element: {e}")
            return None

    def _parse_bio_entities(self, drug_elem, path: str, entity_type: str) -> List[Dict[str, Any]]:
        """Parse target/enzyme/transporter/carrier elements."""
        entities = []

        for entity in drug_elem.findall(path):
            polypeptide = entity.find(f"{DB_NS}polypeptide")
            if polypeptide is None:
                continue

            entity_data = {
                "entity_type": entity_type,
                "drugbank_id": self._get_text(entity, f"{DB_NS}id"),
                "name": self._get_text(entity, f"{DB_NS}name"),
                "organism": self._get_text(entity, f"{DB_NS}organism"),
                "known_action": self._get_text(entity, f"{DB_NS}known-action"),
                "uniprot_id": polypeptide.get("id"),
                "gene_name": self._get_text(polypeptide, f"{DB_NS}gene-name"),
            }

            # Parse actions for targets
            actions = [a.text for a in entity.findall(f"{DB_NS}actions/{DB_NS}action") if a.text]
            entity_data["actions"] = actions

            entities.append(entity_data)

        return entities

    def _get_text(self, elem, path: str) -> Optional[str]:
        """Get text from element path, handling None."""
        child = elem.find(path)
        return child.text.strip() if child is not None and child.text else None

    def _safe_float(self, value: Optional[str]) -> Optional[float]:
        """Convert string to float, handling None and errors."""
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    def transform(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Transform DrugBank record for Neo4j loading.
        """
        drugbank_id = record.get("drugbank_id")
        if not drugbank_id:
            return None

        # Determine if drug is relevant for microbiome studies
        routes = record.get("routes_of_administration", [])
        oral_drug = any(r.lower() in ["oral", "sublingual", "buccal"] for r in routes)

        # Check for known CYP or P-gp involvement (relevant for microbiome interactions)
        enzymes = record.get("enzymes", [])
        cyp_metabolized = any(
            "CYP" in e.get("name", "") or "cytochrome" in e.get("name", "").lower()
            for e in enzymes
        )

        transporters = record.get("transporters", [])
        pgp_substrate = any(
            "P-glycoprotein" in t.get("name", "") or "MDR1" in t.get("name", "") or "ABCB1" in t.get("gene_name", "")
            for t in transporters
        )

        return {
            # Drug properties
            "drug_id": drugbank_id,
            "drugbank_id": drugbank_id,
            "name": record.get("name"),
            "description": record.get("description"),
            "drug_type": record.get("drug_type"),
            "drug_groups": record.get("groups", []),
            "drug_categories": record.get("categories", []),

            # Chemical properties
            "cas_number": record.get("cas_number"),
            "smiles": record.get("smiles"),
            "inchi": record.get("inchi"),
            "inchi_key": record.get("inchi_key"),
            "molecular_weight": record.get("molecular_weight"),
            "chemical_formula": record.get("molecular_formula"),
            "logp": record.get("logp"),

            # Pharmacology
            "mechanism_of_action": record.get("mechanism_of_action"),
            "pharmacodynamics": record.get("pharmacodynamics"),
            "absorption": record.get("absorption"),
            "metabolism": record.get("metabolism"),
            "route_of_administration": routes,
            "atc_codes": record.get("atc_codes", []),

            # Microbiome relevance flags
            "oral_administration": oral_drug,
            "cyp_metabolized": cyp_metabolized,
            "pgp_substrate": pgp_substrate,
            "microbiome_relevant": oral_drug,  # Oral drugs interact with gut microbiome

            # External IDs
            "unii": record.get("external_ids", {}).get("UNII"),
            "pubchem_cid": record.get("external_ids", {}).get("PubChem Compound"),
            "kegg_drug_id": record.get("external_ids", {}).get("KEGG Drug"),
            "chembl_id": record.get("external_ids", {}).get("ChEMBL"),

            # Related entities for relationship creation
            "_targets": record.get("targets", []),
            "_enzymes": record.get("enzymes", []),
            "_transporters": record.get("transporters", []),
            "_carriers": record.get("carriers", []),
            "_food_interactions": record.get("food_interactions", []),

            # Multi-tenant
            "organization_id": self.organization_id,
        }

    def load_batch(self, batch: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Load DrugBank batch into Neo4j.
        """
        if not batch:
            return {"nodes_created": 0, "relationships_created": 0}

        nodes_created = 0
        rels_created = 0

        # Load drug nodes
        nodes_created += self._load_drugs(batch)

        # Load protein nodes and relationships
        for record in batch:
            drug_id = record["drug_id"]

            # Targets
            for target in record.get("_targets", []):
                if target.get("organism", "").lower() == "humans":
                    nodes_created += self._load_protein(target)
                    rels_created += self._load_target_relationship(drug_id, target)

            # Enzymes
            for enzyme in record.get("_enzymes", []):
                if enzyme.get("organism", "").lower() == "humans":
                    nodes_created += self._load_protein(enzyme)
                    rels_created += self._load_enzyme_relationship(drug_id, enzyme)

            # Transporters
            for transporter in record.get("_transporters", []):
                if transporter.get("organism", "").lower() == "humans":
                    nodes_created += self._load_protein(transporter)
                    rels_created += self._load_transporter_relationship(drug_id, transporter)

        return {
            "nodes_created": nodes_created,
            "relationships_created": rels_created
        }

    def _load_drugs(self, batch: List[Dict[str, Any]]) -> int:
        """Load Drug nodes."""
        # Remove internal fields before loading
        drug_records = []
        for record in batch:
            drug_data = {k: v for k, v in record.items() if not k.startswith("_")}
            drug_records.append(drug_data)

        query = """
            UNWIND $drugs AS d
            MERGE (drug:Drug {drug_id: d.drug_id})
            ON CREATE SET
                drug.drugbank_id = d.drugbank_id,
                drug.name = d.name,
                drug.description = d.description,
                drug.drug_type = d.drug_type,
                drug.drug_groups = d.drug_groups,
                drug.drug_categories = d.drug_categories,
                drug.cas_number = d.cas_number,
                drug.smiles = d.smiles,
                drug.inchi = d.inchi,
                drug.inchi_key = d.inchi_key,
                drug.molecular_weight = d.molecular_weight,
                drug.chemical_formula = d.chemical_formula,
                drug.logp = d.logp,
                drug.mechanism_of_action = d.mechanism_of_action,
                drug.pharmacodynamics = d.pharmacodynamics,
                drug.absorption = d.absorption,
                drug.metabolism = d.metabolism,
                drug.route_of_administration = d.route_of_administration,
                drug.atc_codes = d.atc_codes,
                drug.oral_administration = d.oral_administration,
                drug.cyp_metabolized = d.cyp_metabolized,
                drug.pgp_substrate = d.pgp_substrate,
                drug.microbiome_relevant = d.microbiome_relevant,
                drug.unii = d.unii,
                drug.pubchem_cid = d.pubchem_cid,
                drug.kegg_drug_id = d.kegg_drug_id,
                drug.chembl_id = d.chembl_id,
                drug.organization_id = d.organization_id,
                drug.created_at = datetime()
            ON MATCH SET
                drug.updated_at = datetime()
            RETURN count(drug) AS count
        """

        result = self.execute_cypher(query, {"drugs": drug_records})
        return result[0]["count"] if result else 0

    def _load_protein(self, entity: Dict[str, Any]) -> int:
        """Load a Protein node."""
        uniprot_id = entity.get("uniprot_id")
        if not uniprot_id:
            return 0

        query = """
            MERGE (p:Protein {protein_id: $protein_id})
            ON CREATE SET
                p.uniprot_id = $uniprot_id,
                p.name = $name,
                p.gene_name = $gene_name,
                p.is_drug_target = $is_target,
                p.organization_id = $org_id,
                p.created_at = datetime()
            RETURN count(p) AS count
        """

        result = self.execute_cypher(query, {
            "protein_id": f"UniProt:{uniprot_id}",
            "uniprot_id": uniprot_id,
            "name": entity.get("name"),
            "gene_name": entity.get("gene_name"),
            "is_target": entity.get("entity_type") == "target",
            "org_id": self.organization_id,
        })

        return result[0]["count"] if result else 0

    def _load_target_relationship(self, drug_id: str, target: Dict[str, Any]) -> int:
        """Load TARGETS relationship."""
        uniprot_id = target.get("uniprot_id")
        if not uniprot_id:
            return 0

        actions = target.get("actions", [])
        primary_action = actions[0] if actions else None

        query = """
            MATCH (d:Drug {drug_id: $drug_id})
            MATCH (p:Protein {protein_id: $protein_id})
            MERGE (d)-[r:TARGETS]->(p)
            ON CREATE SET
                r.actions = $actions,
                r.action = $primary_action,
                r.known_action = $known_action,
                r.pharmacological_action = $pharmacological,
                r.evidence_source = 'DrugBank',
                r.created_at = datetime()
            RETURN count(r) AS count
        """

        result = self.execute_cypher(query, {
            "drug_id": drug_id,
            "protein_id": f"UniProt:{uniprot_id}",
            "actions": actions,
            "primary_action": primary_action,
            "known_action": target.get("known_action"),
            "pharmacological": target.get("known_action") == "yes",
        })

        return result[0]["count"] if result else 0

    def _load_enzyme_relationship(self, drug_id: str, enzyme: Dict[str, Any]) -> int:
        """Load METABOLIZED_BY relationship."""
        uniprot_id = enzyme.get("uniprot_id")
        if not uniprot_id:
            return 0

        query = """
            MATCH (d:Drug {drug_id: $drug_id})
            MATCH (p:Protein {protein_id: $protein_id})
            MERGE (d)-[r:METABOLIZED_BY]->(p)
            ON CREATE SET
                r.enzyme_name = $name,
                r.gene_name = $gene_name,
                r.known_action = $known_action,
                r.evidence_source = 'DrugBank',
                r.created_at = datetime()
            RETURN count(r) AS count
        """

        result = self.execute_cypher(query, {
            "drug_id": drug_id,
            "protein_id": f"UniProt:{uniprot_id}",
            "name": enzyme.get("name"),
            "gene_name": enzyme.get("gene_name"),
            "known_action": enzyme.get("known_action"),
        })

        return result[0]["count"] if result else 0

    def _load_transporter_relationship(self, drug_id: str, transporter: Dict[str, Any]) -> int:
        """Load TRANSPORTED_BY relationship."""
        uniprot_id = transporter.get("uniprot_id")
        if not uniprot_id:
            return 0

        # Determine transport direction based on transporter type
        name = transporter.get("name", "").lower()
        gene = transporter.get("gene_name", "").upper()

        if "efflux" in name or gene in ["ABCB1", "ABCG2", "ABCC1", "ABCC2"]:
            direction = "efflux"
        elif "uptake" in name or gene.startswith("SLC"):
            direction = "uptake"
        else:
            direction = "unknown"

        query = """
            MATCH (d:Drug {drug_id: $drug_id})
            MATCH (p:Protein {protein_id: $protein_id})
            MERGE (d)-[r:TRANSPORTED_BY]->(p)
            ON CREATE SET
                r.transporter_name = $name,
                r.gene_name = $gene_name,
                r.direction = $direction,
                r.known_action = $known_action,
                r.evidence_source = 'DrugBank',
                r.created_at = datetime()
            RETURN count(r) AS count
        """

        result = self.execute_cypher(query, {
            "drug_id": drug_id,
            "protein_id": f"UniProt:{uniprot_id}",
            "name": transporter.get("name"),
            "gene_name": transporter.get("gene_name"),
            "direction": direction,
            "known_action": transporter.get("known_action"),
        })

        return result[0]["count"] if result else 0


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    from neo4j import GraphDatabase

    load_dotenv()

    NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
    NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

    # DrugBank XML file path
    drugbank_file = os.getenv(
        "DRUGBANK_FILE",
        os.path.join(os.path.dirname(__file__), "..", "..", "data", "drugbank", "drugbank_all_full_database.xml.gz")
    )

    if not os.path.exists(drugbank_file):
        print(f"DrugBank file not found: {drugbank_file}")
        print("Please download from https://go.drugbank.com/releases/latest")
        exit(1)

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    try:
        loader = DrugBankLoader(
            driver=driver,
            organization_id="default",
            file_path=drugbank_file,
            include_experimental=False  # Only approved drugs
        )

        stats = loader.run()
        print(f"Loaded DrugBank data: {stats.to_dict()}")
    finally:
        driver.close()
