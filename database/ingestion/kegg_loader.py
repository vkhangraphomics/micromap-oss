"""
KEGG (Kyoto Encyclopedia of Genes and Genomes) Loader

Loads pathway and metabolic data from KEGG REST API into Neo4j.
Focuses on microbial metabolism pathways relevant to gut microbiome.

API: https://rest.kegg.jp/
"""

from typing import Optional, Iterator, Dict, Any, List
from .base_loader import APIBasedLoader
from database.ingestion.utils import resolve_compound_id
import time
import logging
import re

logger = logging.getLogger(__name__)


# Microbiome-relevant pathway categories
MICROBIOME_PATHWAY_CATEGORIES = {
    # Metabolism
    "01100",  # Metabolic pathways
    "01110",  # Biosynthesis of secondary metabolites
    "01120",  # Microbial metabolism in diverse environments

    # Carbohydrate metabolism (SCFA production)
    "00010",  # Glycolysis
    "00020",  # Citrate cycle (TCA)
    "00030",  # Pentose phosphate pathway
    "00040",  # Pentose and glucuronate interconversions
    "00051",  # Fructose and mannose metabolism
    "00052",  # Galactose metabolism
    "00053",  # Ascorbate and aldarate metabolism
    "00500",  # Starch and sucrose metabolism
    "00520",  # Amino sugar and nucleotide sugar metabolism
    "00620",  # Pyruvate metabolism
    "00630",  # Glyoxylate and dicarboxylate metabolism
    "00640",  # Propanoate metabolism (propionate)
    "00650",  # Butanoate metabolism (butyrate)

    # Amino acid metabolism
    "00250",  # Alanine, aspartate and glutamate metabolism
    "00260",  # Glycine, serine and threonine metabolism
    "00270",  # Cysteine and methionine metabolism
    "00280",  # Valine, leucine and isoleucine degradation
    "00290",  # Valine, leucine and isoleucine biosynthesis
    "00300",  # Lysine biosynthesis
    "00310",  # Lysine degradation
    "00330",  # Arginine and proline metabolism
    "00340",  # Histidine metabolism
    "00350",  # Tyrosine metabolism
    "00360",  # Phenylalanine metabolism
    "00380",  # Tryptophan metabolism (indoles)
    "00400",  # Phenylalanine, tyrosine and tryptophan biosynthesis

    # Lipid metabolism
    "00061",  # Fatty acid biosynthesis
    "00071",  # Fatty acid degradation
    "00120",  # Primary bile acid biosynthesis
    "00121",  # Secondary bile acid biosynthesis

    # Other relevant pathways
    "00680",  # Methane metabolism
    "00910",  # Nitrogen metabolism
    "00920",  # Sulfur metabolism
    "00970",  # Aminoacyl-tRNA biosynthesis

    # Drug metabolism
    "00982",  # Drug metabolism - cytochrome P450
    "00983",  # Drug metabolism - other enzymes
}


class KEGGLoader(APIBasedLoader):
    """
    Load KEGG pathway data into Neo4j.

    Creates:
    - Pathway nodes with hierarchy and category information
    - Compound (metabolite) nodes
    - Reaction nodes
    - Gene/KO (KEGG Orthology) associations
    - Pathway-compound relationships
    """

    @property
    def source_name(self) -> str:
        return "KEGG"

    def __init__(
        self,
        driver,
        organization_id: str,
        batch_size: int = 100,
        database: str = "neo4j",
        pathway_filter: Optional[List[str]] = None
    ):
        """
        Initialize the KEGG loader.

        Args:
            driver: Neo4j driver
            organization_id: Organization ID
            batch_size: Records per batch
            database: Neo4j database name
            pathway_filter: Optional list of pathway IDs to load (default: microbiome-relevant)
        """
        super().__init__(
            driver,
            organization_id,
            api_base_url="https://rest.kegg.jp",
            batch_size=batch_size,
            database=database,
            rate_limit_delay=0.4  # KEGG rate limit: ~10 requests/second
        )

        self.pathway_filter = set(pathway_filter) if pathway_filter else MICROBIOME_PATHWAY_CATEGORIES

    def extract(self, **kwargs) -> Iterator[Dict[str, Any]]:
        """
        Extract pathway data from KEGG REST API.

        For each pathway, fetches:
        - Pathway metadata
        - Associated compounds
        - Associated genes/KOs
        """
        logger.info("Fetching KEGG pathway list...")

        # Get list of all pathways
        pathways = self._fetch_pathway_list()
        logger.info(f"Found {len(pathways)} total pathways")

        # Filter to microbiome-relevant pathways
        filtered_pathways = [
            p for p in pathways
            if any(p["pathway_id"].endswith(cat) for cat in self.pathway_filter)
        ]
        logger.info(f"Filtering to {len(filtered_pathways)} microbiome-relevant pathways")

        for i, pathway_info in enumerate(filtered_pathways):
            pathway_id = pathway_info["pathway_id"]
            logger.info(f"Processing pathway {i+1}/{len(filtered_pathways)}: {pathway_id}")

            try:
                # Fetch detailed pathway info
                pathway_data = self._fetch_pathway_details(pathway_id)
                if pathway_data:
                    pathway_data["basic_info"] = pathway_info
                    yield pathway_data

            except Exception as e:
                logger.warning(f"Failed to fetch pathway {pathway_id}: {e}")
                continue

    def _fetch_pathway_list(self) -> List[Dict[str, Any]]:
        """Fetch list of all reference pathways."""
        url = f"{self.api_base_url}/list/pathway"
        response = self._fetch_text(url)

        pathways = []
        for line in response.strip().split("\n"):
            if line:
                parts = line.split("\t")
                if len(parts) >= 2:
                    pathway_id = parts[0].replace("path:", "")
                    name = parts[1]
                    pathways.append({
                        "pathway_id": pathway_id,
                        "name": name
                    })

        return pathways

    def _fetch_pathway_details(self, pathway_id: str) -> Optional[Dict[str, Any]]:
        """Fetch detailed pathway information."""
        # Get pathway entry
        url = f"{self.api_base_url}/get/{pathway_id}"
        text_response = self._fetch_text(url)

        if not text_response:
            return None

        # Parse the flat file format
        data = self._parse_kegg_flat_file(text_response)

        # Get associated compounds
        compounds = self._fetch_pathway_compounds(pathway_id)

        # Get associated KOs
        kos = self._fetch_pathway_kos(pathway_id)

        return {
            "pathway_id": pathway_id,
            "name": data.get("NAME", [""])[0],
            "description": data.get("DESCRIPTION", [""])[0] if "DESCRIPTION" in data else None,
            "class": data.get("CLASS", [""])[0] if "CLASS" in data else None,
            "module": data.get("MODULE", []),
            "compounds": compounds,
            "kos": kos,
            "dblinks": data.get("DBLINKS", {}),
        }

    def _fetch_pathway_compounds(self, pathway_id: str) -> List[Dict[str, Any]]:
        """Fetch compounds associated with a pathway."""
        # Use reference pathway (map) or organism-specific
        map_id = pathway_id.replace("ko", "map").replace("ec", "map")
        if not map_id.startswith("map"):
            map_id = f"map{pathway_id[-5:]}"

        url = f"{self.api_base_url}/link/compound/{map_id}"
        response = self._fetch_text(url)

        compounds = []
        for line in response.strip().split("\n"):
            if line:
                parts = line.split("\t")
                if len(parts) >= 2:
                    compound_id = parts[1].replace("cpd:", "")
                    compounds.append({"compound_id": compound_id})

        # Fetch compound details for each
        for i, compound in enumerate(compounds):
            if i >= 50:  # Limit to avoid too many API calls
                break
            details = self._fetch_compound_details(compound["compound_id"])
            if details:
                compound.update(details)

        return compounds

    def _fetch_compound_details(self, compound_id: str) -> Optional[Dict[str, Any]]:
        """Fetch details for a single compound."""
        url = f"{self.api_base_url}/get/cpd:{compound_id}"
        text_response = self._fetch_text(url)

        if not text_response:
            return None

        data = self._parse_kegg_flat_file(text_response)

        # Extract key fields
        names = data.get("NAME", [])
        name = names[0].rstrip(";") if names else None

        formula = data.get("FORMULA", [""])[0] if "FORMULA" in data else None
        exact_mass = data.get("EXACT_MASS", [""])[0] if "EXACT_MASS" in data else None

        # Get cross-references
        dblinks = data.get("DBLINKS", {})

        return {
            "name": name,
            "formula": formula,
            "exact_mass": self._safe_float(exact_mass),
            "chebi_id": dblinks.get("ChEBI"),
            "pubchem_cid": dblinks.get("PubChem"),
            "hmdb_id": dblinks.get("HMDB"),
        }

    def _fetch_pathway_kos(self, pathway_id: str) -> List[Dict[str, Any]]:
        """Fetch KEGG Orthology entries for a pathway."""
        url = f"{self.api_base_url}/link/ko/{pathway_id}"
        response = self._fetch_text(url)

        kos = []
        for line in response.strip().split("\n"):
            if line:
                parts = line.split("\t")
                if len(parts) >= 2:
                    ko_id = parts[1].replace("ko:", "")
                    kos.append({"ko_id": ko_id})

        return kos

    def _parse_kegg_flat_file(self, text: str) -> Dict[str, Any]:
        """Parse KEGG flat file format into dictionary."""
        data = {}
        current_field = None
        current_values = []

        for line in text.split("\n"):
            if not line:
                continue

            # Check if this is a new field
            if line[0] != " " and line[0] != "\t":
                # Save previous field
                if current_field:
                    data[current_field] = current_values

                # Parse new field
                parts = line.split(None, 1)
                current_field = parts[0]
                current_values = [parts[1]] if len(parts) > 1 else []

                # Special handling for DBLINKS
                if current_field == "DBLINKS" and len(parts) > 1:
                    dblinks = {}
                    match = re.match(r"(\w+):\s*(.+)", parts[1])
                    if match:
                        dblinks[match.group(1)] = match.group(2).strip()
                    data["DBLINKS"] = dblinks
                    current_field = None
                    current_values = []

            else:
                # Continuation of previous field
                value = line.strip()
                if current_field == "DBLINKS":
                    match = re.match(r"(\w+):\s*(.+)", value)
                    if match:
                        data.setdefault("DBLINKS", {})[match.group(1)] = match.group(2).strip()
                else:
                    current_values.append(value)

        # Save last field
        if current_field and current_field != "DBLINKS":
            data[current_field] = current_values

        return data

    def _fetch_text(self, url: str) -> str:
        """Fetch text response from KEGG API."""
        import requests

        time.sleep(self.rate_limit_delay)

        try:
            response = requests.get(url, timeout=30)
            if response.status_code == 200:
                return response.text
            elif response.status_code == 404:
                return ""
            else:
                response.raise_for_status()
        except Exception as e:
            logger.warning(f"KEGG API error for {url}: {e}")
            return ""

        return ""

    def _safe_float(self, value: Optional[str]) -> Optional[float]:
        """Convert to float safely."""
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    def transform(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Transform KEGG record for Neo4j loading."""
        pathway_id = record.get("pathway_id")
        if not pathway_id:
            return None

        # Parse pathway class hierarchy
        class_str = record.get("class", "")
        category = None
        subcategory = None
        if class_str:
            parts = class_str.split(";")
            if len(parts) >= 1:
                category = parts[0].strip()
            if len(parts) >= 2:
                subcategory = parts[1].strip()

        # Determine if microbial pathway
        is_microbial = any(
            pathway_id.endswith(cat) for cat in MICROBIOME_PATHWAY_CATEGORIES
        )

        return {
            "pathway_id": f"KEGG:{pathway_id}",
            "kegg_id": pathway_id,
            "name": record.get("name"),
            "description": record.get("description"),
            "category": category,
            "subcategory": subcategory,
            "pathway_type": "metabolic" if "metabolism" in (category or "").lower() else "other",
            "microbial_pathway": is_microbial,
            "host_pathway": True,  # All KEGG pathways can be in host

            # Compounds for relationship creation
            "_compounds": record.get("compounds", []),
            "_kos": record.get("kos", []),

            # Multi-tenant
            "organization_id": self.organization_id,
        }

    def load_batch(self, batch: List[Dict[str, Any]]) -> Dict[str, int]:
        """Load KEGG batch into Neo4j."""
        if not batch:
            return {"nodes_created": 0, "relationships_created": 0}

        nodes_created = 0
        rels_created = 0

        # Load pathway nodes
        nodes_created += self._load_pathways(batch)

        # Load compounds and relationships
        for record in batch:
            pathway_id = record["pathway_id"]

            for compound in record.get("_compounds", []):
                nodes_created += self._load_compound(compound)
                rels_created += self._load_pathway_compound_rel(pathway_id, compound)

        return {
            "nodes_created": nodes_created,
            "relationships_created": rels_created
        }

    def _load_pathways(self, batch: List[Dict[str, Any]]) -> int:
        """Load Pathway nodes."""
        pathway_records = []
        for record in batch:
            p = {k: v for k, v in record.items() if not k.startswith("_")}
            pathway_records.append(p)

        query = """
            UNWIND $pathways AS p
            MERGE (pathway:Pathway {pathway_id: p.pathway_id})
            ON CREATE SET
                pathway.kegg_id = p.kegg_id,
                pathway.name = p.name,
                pathway.description = p.description,
                pathway.category = p.category,
                pathway.subcategory = p.subcategory,
                pathway.pathway_type = p.pathway_type,
                pathway.microbial_pathway = p.microbial_pathway,
                pathway.host_pathway = p.host_pathway,
                pathway.organization_id = p.organization_id,
                pathway.created_at = datetime()
            ON MATCH SET
                pathway.updated_at = datetime()
            RETURN count(pathway) AS count
        """

        result = self.execute_cypher(query, {"pathways": pathway_records})
        return result[0]["count"] if result else 0

    def _load_compound(self, compound: Dict[str, Any]) -> int:
        """Load a KEGG compound as Compound node."""
        try:
            compound_id = resolve_compound_id(
                compound.get("inchi_key"),
                compound.get("pubchem_cid"),
            )
        except ValueError:
            return 0

        query = """
            MERGE (m:Compound {compound_id: $compound_id})
            ON CREATE SET
                m.kegg_id = $kegg_id,
                m.name = $name,
                m.chemical_formula = $formula,
                m.monoisotopic_mass = $mass,
                m.chebi_id = $chebi_id,
                m.pubchem_cid = $pubchem_cid,
                m.inchi_key = $inchi_key,
                m.organization_id = $org_id,
                m.created_at = datetime()
            ON MATCH SET
                m.kegg_id = COALESCE(m.kegg_id, $kegg_id),
                m.name = COALESCE(m.name, $name),
                m.updated_at = datetime()
            RETURN count(m) AS count
        """
        result = self.execute_cypher(query, {
            "compound_id": compound_id,
            "kegg_id": compound.get("kegg_id"),
            "name": compound.get("name"),
            "formula": compound.get("chemical_formula"),
            "mass": compound.get("monoisotopic_mass"),
            "chebi_id": compound.get("chebi_id"),
            "pubchem_cid": compound.get("pubchem_cid"),
            "inchi_key": compound.get("inchi_key"),
            "org_id": self.organization_id,
        })
        return result[0]["count"] if result else 0

    def _load_pathway_compound_rel(self, pathway_id: str, compound: Dict[str, Any]) -> int:
        """Load pathway-compound relationship."""
        try:
            compound_id = resolve_compound_id(
                compound.get("inchi_key"),
                compound.get("pubchem_cid"),
            )
        except ValueError:
            return 0

        query = """
            MATCH (p:Pathway {pathway_id: $pathway_id})
            MATCH (m:Compound {compound_id: $compound_id})
            MERGE (m)-[r:PARTICIPATES_IN]->(p)
            ON CREATE SET
                r.evidence_source = 'KEGG',
                r.created_at = datetime()
            RETURN count(r) AS count
        """

        result = self.execute_cypher(query, {
            "pathway_id": pathway_id,
            "compound_id": compound_id,
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

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    try:
        loader = KEGGLoader(
            driver=driver,
            organization_id="default",
        )

        stats = loader.run()
        print(f"Loaded KEGG data: {stats.to_dict()}")
    finally:
        driver.close()
