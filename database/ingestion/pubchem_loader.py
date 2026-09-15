"""
PubChem Data Loader

Loads compound and bioactivity data from PubChem database into Neo4j.
PubChem is a free chemical database maintained by NCBI.

License: Public Domain - No restrictions on use.

Data source: https://pubchem.ncbi.nlm.nih.gov/
API docs: https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest
"""

from typing import Optional, Iterator, Dict, Any, List
from .base_loader import APIBasedLoader
from database.ingestion.utils import resolve_compound_id
import logging
import time

logger = logging.getLogger(__name__)


class PubChemLoader(APIBasedLoader):
    """
    Load compound and bioactivity data from PubChem PUG REST API.

    Creates:
    - Compound nodes (small molecules from PubChem)
    - Assay nodes (bioactivity assays)
    - TESTED_IN relationships (Compound -> Assay)
    - Links to existing Drug nodes from ChEMBL via InChIKey

    Uses PubChem PUG REST API to fetch:
    - Compound properties (by CID or name search)
    - Bioactivity data (assays where compound is active)
    """

    @property
    def source_name(self) -> str:
        return "PubChem"

    def __init__(
        self,
        driver,
        organization_id: str,
        batch_size: int = 500,
        database: str = "neo4j",
        compound_cids: Optional[List[int]] = None,
        search_terms: Optional[List[str]] = None,
        max_compounds: Optional[int] = None,
        include_bioactivity: bool = True
    ):
        """
        Initialize the PubChem loader.

        Args:
            driver: Neo4j driver
            organization_id: Organization ID for multi-tenant isolation
            batch_size: Records per batch
            database: Neo4j database name
            compound_cids: List of specific PubChem CIDs to load
            search_terms: List of compound names/terms to search
            max_compounds: Maximum number of compounds to load (None = all found)
            include_bioactivity: Whether to load bioactivity assay data
        """
        super().__init__(
            driver,
            organization_id,
            api_base_url="https://pubchem.ncbi.nlm.nih.gov/rest/pug",
            batch_size=batch_size,
            database=database,
            rate_limit_delay=0.35  # PubChem allows 5 requests/second
        )
        self.compound_cids = compound_cids or []
        self.search_terms = search_terms or []
        self.max_compounds = max_compounds
        self.include_bioactivity = include_bioactivity
        self._assay_cache: Dict[int, Dict] = {}

    def extract(self, **kwargs) -> Iterator[Dict[str, Any]]:
        """
        Extract compound data from PubChem.

        Yields:
            Compound records with properties and optional bioactivity
        """
        cids_to_fetch = list(self.compound_cids)

        # Search for compounds by name if search terms provided
        if self.search_terms:
            logger.info(f"Searching PubChem for {len(self.search_terms)} terms...")
            for term in self.search_terms:
                found_cids = self._search_compound(term)
                cids_to_fetch.extend(found_cids)
                logger.info(f"  '{term}' -> {len(found_cids)} CIDs found")

        # Remove duplicates while preserving order
        cids_to_fetch = list(dict.fromkeys(cids_to_fetch))

        if self.max_compounds and len(cids_to_fetch) > self.max_compounds:
            cids_to_fetch = cids_to_fetch[:self.max_compounds]

        logger.info(f"Fetching {len(cids_to_fetch)} compounds from PubChem...")

        # Fetch compounds in batches (PUG REST supports up to 200 CIDs per request)
        batch_size = 100  # Conservative batch size
        for i in range(0, len(cids_to_fetch), batch_size):
            batch_cids = cids_to_fetch[i:i + batch_size]
            compounds = self._fetch_compound_batch(batch_cids)

            for compound in compounds:
                cid = compound.get("CID")
                if not cid:
                    continue

                # Optionally fetch bioactivity data
                bioactivity = []
                if self.include_bioactivity:
                    bioactivity = self._fetch_bioactivity(cid)

                yield {
                    "compound": compound,
                    "bioactivity": bioactivity
                }

            if (i + batch_size) % 500 == 0:
                logger.info(f"Processed {min(i + batch_size, len(cids_to_fetch))}/{len(cids_to_fetch)} compounds...")

    def _search_compound(self, term: str) -> List[int]:
        """Search PubChem for compounds matching a term."""
        url = f"{self.api_base_url}/compound/name/{term}/cids/JSON"

        try:
            response = self.fetch_with_retry(url)
            return response.get("IdentifierList", {}).get("CID", [])
        except Exception as e:
            # Try autocomplete search as fallback
            logger.warning(f"Name search failed for '{term}': {e}. Trying autocomplete...")
            return self._autocomplete_search(term)

    def _autocomplete_search(self, term: str) -> List[int]:
        """Use autocomplete API for fuzzy matching."""
        url = f"https://pubchem.ncbi.nlm.nih.gov/rest/autocomplete/compound/{term}/json"

        try:
            time.sleep(self.rate_limit_delay)
            import requests
            response = requests.get(url, params={"limit": 10}, timeout=30)
            response.raise_for_status()
            data = response.json()

            suggestions = data.get("dictionary_terms", {}).get("compound", [])
            cids = []
            for suggestion in suggestions[:5]:  # Limit to top 5 matches
                found = self._search_compound(suggestion)
                cids.extend(found[:3])  # Take top 3 per suggestion
            return cids
        except Exception as e:
            logger.warning(f"Autocomplete search failed for '{term}': {e}")
            return []

    def _fetch_compound_batch(self, cids: List[int]) -> List[Dict]:
        """Fetch compound properties for a batch of CIDs."""
        if not cids:
            return []

        cid_str = ",".join(map(str, cids))
        url = f"{self.api_base_url}/compound/cid/{cid_str}/property/MolecularFormula,MolecularWeight,IUPACName,InChIKey,CanonicalSMILES,XLogP,TPSA,HBondDonorCount,HBondAcceptorCount,RotatableBondCount,Complexity/JSON"

        try:
            response = self.fetch_with_retry(url)
            return response.get("PropertyTable", {}).get("Properties", [])
        except Exception as e:
            logger.warning(f"Error fetching compound batch: {e}")
            return []

    def _fetch_bioactivity(self, cid: int) -> List[Dict]:
        """Fetch bioactivity data for a compound."""
        url = f"{self.api_base_url}/compound/cid/{cid}/assaysummary/JSON"

        try:
            response = self.fetch_with_retry(url)
            # Extract active assays only
            table = response.get("Table", {})
            columns = table.get("Columns", {}).get("Column", [])
            rows = table.get("Row", [])

            if not columns or not rows:
                return []

            # Parse table into list of dicts
            results = []
            for row in rows[:20]:  # Limit to 20 most relevant assays
                cells = row.get("Cell", [])
                if len(cells) >= len(columns):
                    # `cells` may be longer than `columns`; zip takes the first
                    # len(columns) cells by design (strict=False).
                    record = dict(zip(columns, cells, strict=False))
                    # Only include active results
                    if record.get("Activity Outcome") == "Active":
                        results.append({
                            "aid": record.get("AID"),
                            "assay_name": record.get("Assay Name"),
                            "activity_value": record.get("Activity Value"),
                            "target_name": record.get("Target Name"),
                            "target_gi": record.get("Target GI")
                        })
            return results
        except Exception as e:
            # Bioactivity data not always available
            logger.debug(f"No bioactivity data for CID {cid}: {e}")
            return []

    def transform(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Transform PubChem record to graph format.

        Args:
            record: Raw record with compound and bioactivity

        Returns:
            Transformed record for Neo4j
        """
        compound = record.get("compound", {})
        bioactivity = record.get("bioactivity", [])

        if not compound:
            return None

        cid = compound.get("CID")
        if not cid:
            return None

        result = {
            # Compound info
            "compound_id": resolve_compound_id(compound.get("InChIKey"), cid),
            "pubchem_cid": cid,
            "name": compound.get("IUPACName") or f"CID{cid}",
            "molecular_formula": compound.get("MolecularFormula"),
            "molecular_weight": compound.get("MolecularWeight"),
            "smiles": compound.get("CanonicalSMILES"),
            "inchi_key": compound.get("InChIKey"),
            "xlogp": compound.get("XLogP"),
            "tpsa": compound.get("TPSA"),
            "hbd": compound.get("HBondDonorCount"),
            "hba": compound.get("HBondAcceptorCount"),
            "rotatable_bonds": compound.get("RotatableBondCount"),
            "complexity": compound.get("Complexity"),

            # Bioactivity
            "bioactivity": bioactivity,

            # Organization
            "organization_id": self.organization_id,
        }

        return result

    def load_batch(self, batch: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Load a batch of PubChem records into Neo4j.

        Creates:
        - Compound nodes
        - Assay nodes (if bioactivity data present)
        - TESTED_IN relationships
        - SAME_AS relationships from Compound to ChEMBL Drug (matching InChIKey)
        - SAME_AS relationships from Compound to Metabolite (matching InChIKey
          or pubchem_cid). See issue #46 / umbrella #42.
        """
        if not batch:
            return {"nodes_created": 0, "relationships_created": 0}

        nodes_created = 0
        rels_created = 0

        # Load compounds
        nodes_created += self._load_compounds(batch)

        # Load assays and relationships
        if self.include_bioactivity:
            assay_count, rel_count = self._load_bioactivity(batch)
            nodes_created += assay_count
            rels_created += rel_count

        # Link to existing ChEMBL drugs by InChI key
        rels_created += self._link_to_chembl(batch)

        # Link to existing Metabolite nodes by InChI key or pubchem_cid so the
        # chemical-entity hierarchy unifies across labels.
        rels_created += self._link_to_metabolites(batch)

        return {
            "nodes_created": nodes_created,
            "relationships_created": rels_created
        }

    def _load_compounds(self, batch: List[Dict[str, Any]]) -> int:
        """Load Compound nodes from batch."""
        # Deduplicate compounds
        compounds = {}
        for record in batch:
            compound_id = record.get("compound_id")
            if compound_id and compound_id not in compounds:
                compounds[compound_id] = {
                    "compound_id": compound_id,
                    "pubchem_cid": record.get("pubchem_cid"),
                    "name": record.get("name"),
                    "molecular_formula": record.get("molecular_formula"),
                    "molecular_weight": record.get("molecular_weight"),
                    "smiles": record.get("smiles"),
                    "inchi_key": record.get("inchi_key"),
                    "xlogp": record.get("xlogp"),
                    "tpsa": record.get("tpsa"),
                    "hbd": record.get("hbd"),
                    "hba": record.get("hba"),
                    "rotatable_bonds": record.get("rotatable_bonds"),
                    "complexity": record.get("complexity"),
                    "organization_id": self.organization_id,
                }

        if not compounds:
            return 0

        query = """
            UNWIND $compounds AS c
            MERGE (compound:Compound {compound_id: c.compound_id})
            ON CREATE SET
                compound.pubchem_cid = c.pubchem_cid,
                compound.name = c.name,
                compound.molecular_formula = c.molecular_formula,
                compound.molecular_weight = c.molecular_weight,
                compound.smiles = c.smiles,
                compound.inchi_key = c.inchi_key,
                compound.xlogp = c.xlogp,
                compound.tpsa = c.tpsa,
                compound.hbd = c.hbd,
                compound.hba = c.hba,
                compound.rotatable_bonds = c.rotatable_bonds,
                compound.complexity = c.complexity,
                compound.organization_id = c.organization_id,
                compound.source = 'pubchem',
                compound.sources = ['pubchem'],
                compound.created_at = datetime()
            RETURN count(compound) AS count
        """

        result = self.execute_cypher(query, {"compounds": list(compounds.values())})
        return result[0]["count"] if result else 0

    def _load_bioactivity(self, batch: List[Dict[str, Any]]) -> tuple:
        """Load Assay nodes and TESTED_IN relationships."""
        assays = {}
        relationships = []

        for record in batch:
            compound_id = record.get("compound_id")
            bioactivity = record.get("bioactivity", [])

            for activity in bioactivity:
                aid = activity.get("aid")
                if aid:
                    assay_id = f"PUBCHEM_ASSAY:{aid}"

                    # Collect unique assays
                    if assay_id not in assays:
                        assays[assay_id] = {
                            "assay_id": assay_id,
                            "pubchem_aid": aid,
                            "name": activity.get("assay_name"),
                            "target_name": activity.get("target_name"),
                            "organization_id": self.organization_id,
                        }

                    # Collect relationships
                    relationships.append({
                        "compound_id": compound_id,
                        "assay_id": assay_id,
                        "activity_value": activity.get("activity_value"),
                    })

        assay_count = 0
        rel_count = 0

        # Load assays
        if assays:
            query = """
                UNWIND $assays AS a
                MERGE (assay:Assay {assay_id: a.assay_id})
                ON CREATE SET
                    assay.pubchem_aid = a.pubchem_aid,
                    assay.name = a.name,
                    assay.target_name = a.target_name,
                    assay.organization_id = a.organization_id,
                    assay.source = 'pubchem',
                    assay.sources = ['pubchem'],
                    assay.created_at = datetime()
                RETURN count(assay) AS count
            """
            result = self.execute_cypher(query, {"assays": list(assays.values())})
            assay_count = result[0]["count"] if result else 0

        # Load relationships
        if relationships:
            query = """
                UNWIND $relationships AS r
                MATCH (compound:Compound {compound_id: r.compound_id})
                MATCH (assay:Assay {assay_id: r.assay_id})
                MERGE (compound)-[rel:TESTED_IN]->(assay)
                ON CREATE SET
                    rel.activity_value = r.activity_value,
                    rel.source = 'pubchem',
                    rel.created_at = datetime()
                RETURN count(rel) AS count
            """
            result = self.execute_cypher(query, {"relationships": relationships})
            rel_count = result[0]["count"] if result else 0

        return assay_count, rel_count

    def _link_to_chembl(self, batch: List[Dict[str, Any]]) -> int:
        """Link PubChem compounds to ChEMBL drugs with matching InChIKey."""
        # Get compounds with InChIKey
        compounds_with_key = [
            {"compound_id": r["compound_id"], "inchi_key": r["inchi_key"]}
            for r in batch
            if r.get("inchi_key")
        ]

        if not compounds_with_key:
            return 0

        # Find matching ChEMBL drugs and create SAME_AS relationships
        query = """
            UNWIND $compounds AS c
            MATCH (compound:Compound {compound_id: c.compound_id})
            MATCH (drug:Drug {inchi_key: c.inchi_key})
            WHERE drug.source = 'chembl'
            MERGE (compound)-[r:SAME_AS]->(drug)
            ON CREATE SET
                r.match_type = 'inchi_key',
                r.created_at = datetime()
            RETURN count(r) AS count
        """

        result = self.execute_cypher(query, {"compounds": compounds_with_key})
        return result[0]["count"] if result else 0

    def _link_to_metabolites(self, batch: List[Dict[str, Any]]) -> int:
        """Link PubChem compounds to existing Metabolite nodes (issue #46).

        Match strategy (per the Option B decision recorded on the issue):
        1. InChI key — preferred; an InChIKey collision is the strongest
           cross-source signal that two chemical-entity nodes are the same.
        2. PubChem CID — fallback; some Metabolite nodes carry a
           pubchem_cid set by HMDB/KEGG ingestion even when an InChIKey was
           not preserved.

        Edges are emitted as Compound -[:SAME_AS {match_type}]-> Metabolite,
        mirroring the Compound→Drug SAME_AS pattern already produced by
        `_link_to_chembl`.
        """
        # Compounds with at least one usable cross-reference
        compounds_with_xref = [
            {
                "compound_id": r["compound_id"],
                "inchi_key": r.get("inchi_key"),
                "pubchem_cid": r.get("pubchem_cid"),
            }
            for r in batch
            if r.get("compound_id") and (r.get("inchi_key") or r.get("pubchem_cid"))
        ]

        if not compounds_with_xref:
            return 0

        query = """
            UNWIND $compounds AS c
            MATCH (compound:Compound {compound_id: c.compound_id})
            MATCH (other_compound:Compound)
            WHERE (c.inchi_key IS NOT NULL
                   AND other_compound.inchi_key = c.inchi_key)
               OR (c.pubchem_cid IS NOT NULL
                   AND other_compound.pubchem_cid = c.pubchem_cid)
            MERGE (compound)-[r:SAME_AS]->(other_compound)
            ON CREATE SET
                r.match_type = CASE
                    WHEN other_compound.inchi_key = c.inchi_key
                         AND c.inchi_key IS NOT NULL THEN 'inchi_key'
                    ELSE 'pubchem_cid'
                END,
                r.source = 'pubchem',
                r.created_at = datetime()
            RETURN count(r) AS count
        """

        result = self.execute_cypher(query, {"compounds": compounds_with_xref})
        return result[0]["count"] if result else 0


# Convenience functions for common use cases

def load_microbiome_related_compounds(driver, organization_id: str = "default") -> Dict[str, Any]:
    """
    Load compounds related to microbiome research.

    Includes:
    - Short-chain fatty acids (SCFAs)
    - Bile acids
    - Amino acid metabolites
    - Common antibiotics
    """
    search_terms = [
        # SCFAs
        "butyric acid",
        "acetic acid",
        "propionic acid",
        "valeric acid",
        "isobutyric acid",
        "isovaleric acid",

        # Bile acids
        "cholic acid",
        "deoxycholic acid",
        "lithocholic acid",
        "ursodeoxycholic acid",
        "chenodeoxycholic acid",

        # Amino acid metabolites
        "indole",
        "indole-3-acetic acid",
        "tryptamine",
        "tyramine",
        "histamine",
        "putrescine",
        "cadaverine",
        "trimethylamine",
        "trimethylamine N-oxide",

        # Other microbiome-relevant
        "lipopolysaccharide",
        "peptidoglycan",
        "butyrate",
        "propionate",
        "acetate",
    ]

    loader = PubChemLoader(
        driver=driver,
        organization_id=organization_id,
        search_terms=search_terms,
        include_bioactivity=False  # Skip bioactivity for faster loading
    )

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
        # Test with a few specific compounds
        loader = PubChemLoader(
            driver=driver,
            organization_id="default",
            search_terms=["butyric acid", "trimethylamine N-oxide", "indole"],
            include_bioactivity=False  # Skip for faster test
        )

        stats = loader.run()
        print("\nPubChem Loading Complete!")
        print(f"  Nodes created: {stats.nodes_created}")
        print(f"  Relationships created: {stats.relationships_created}")
        print(f"  Duration: {stats.duration_seconds:.1f} seconds")
    finally:
        driver.close()
