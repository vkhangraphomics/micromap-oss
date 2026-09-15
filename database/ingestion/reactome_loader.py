"""
Reactome Pathway Loader

Loads pathway data from the Reactome Content Service REST API into Neo4j.
Focuses on human pathways relevant to microbiome research:
- Metabolism pathways
- Immune system pathways
- Signal transduction pathways (gut-brain axis)

API: https://reactome.org/ContentService
"""

from typing import Optional, Iterator, Dict, Any, List
from .base_loader import APIBasedLoader
import logging

logger = logging.getLogger(__name__)


# Top-level Reactome pathway categories relevant to microbiome research
MICROBIOME_RELEVANT_CATEGORIES = {
    "R-HSA-1430728": "Metabolism",
    "R-HSA-168256": "Immune System",
    "R-HSA-162582": "Signal Transduction",
}

# Keywords to filter sub-pathways for microbiome relevance
MICROBIOME_PATHWAY_KEYWORDS = [
    # Metabolism
    "amino acid", "tryptophan", "tyrosine", "phenylalanine", "histidine",
    "arginine", "proline", "glutamate", "glutamine", "methionine", "cysteine",
    "bile acid", "bile salt", "fatty acid", "short-chain fatty acid", "scfa",
    "butyrate", "propionate", "acetate", "lipid", "cholesterol",
    "carbohydrate", "glucose", "fructose", "galactose", "pyruvate",
    "citric acid", "tca cycle", "glycolysis",
    "folate", "vitamin", "biotin", "thiamine", "riboflavin", "cobalamin",
    "niacin", "pantothenate",
    "xenobiotic", "drug metabolism",
    # Immune
    "innate immune", "adaptive immune", "toll-like receptor", "tlr",
    "nod-like receptor", "nlr", "inflammasome",
    "cytokine", "interleukin", "interferon", "nf-kb", "tnf",
    "antimicrobial peptide", "defensin",
    "complement", "phagocytosis",
    "t cell", "b cell", "dendritic cell", "macrophage", "neutrophil",
    "immunoglobulin", "iga",
    # Signal transduction
    "gpcr", "g protein-coupled",
    "wnt", "notch", "hedgehog",
    "mapk", "erk", "jnk",
    "pi3k", "akt", "mtor",
    "jak-stat", "tgf-beta",
    "serotonin", "dopamine", "gaba",
    "neurotransmitter", "neuropeptide",
]


class ReactomeLoader(APIBasedLoader):
    """
    Load Reactome pathway data into Neo4j.

    Creates:
    - Pathway nodes with Reactome identifiers
    - PARTICIPATES_IN relationships linking existing Metabolite nodes (via ChEBI cross-references)
    - PARTICIPATES_IN relationships linking existing Gene/Protein nodes where possible
    """

    @property
    def source_name(self) -> str:
        return "Reactome"

    def __init__(
        self,
        driver,
        organization_id: str,
        batch_size: int = 100,
        database: str = "neo4j",
        max_pathways: Optional[int] = None,
    ):
        """
        Initialize the Reactome loader.

        Args:
            driver: Neo4j driver
            organization_id: Organization ID
            batch_size: Records per batch
            database: Neo4j database name
            max_pathways: Optional limit on total pathways to load (for testing)
        """
        super().__init__(
            driver,
            organization_id,
            api_base_url="https://reactome.org/ContentService",
            batch_size=batch_size,
            database=database,
            rate_limit_delay=0.3,
        )
        self.max_pathways = max_pathways

    def extract(self, **kwargs) -> Iterator[Dict[str, Any]]:
        """
        Extract pathway data from Reactome Content Service API.

        Fetches top-level human pathways in microbiome-relevant categories,
        then retrieves sub-pathways and their participants.
        """
        logger.info("Fetching Reactome top-level human pathways...")

        count = 0
        for top_pathway_id, category_name in MICROBIOME_RELEVANT_CATEGORIES.items():
            logger.info(f"Processing Reactome category: {category_name} ({top_pathway_id})")

            # Get sub-pathways for this top-level category
            sub_pathways = self._fetch_contained_events(top_pathway_id)
            logger.info(f"Found {len(sub_pathways)} sub-pathways in {category_name}")

            # Filter to microbiome-relevant sub-pathways
            relevant = self._filter_relevant_pathways(sub_pathways)
            logger.info(f"Filtered to {len(relevant)} microbiome-relevant pathways in {category_name}")

            for pathway_info in relevant:
                if self.max_pathways and count >= self.max_pathways:
                    return

                pathway_id = pathway_info.get("stId")
                if not pathway_id:
                    continue

                try:
                    # Fetch pathway details
                    details = self._fetch_pathway_details(pathway_id)
                    if details:
                        details["_category"] = category_name
                        # Fetch participants (molecules)
                        participants = self._fetch_participants(pathway_id)
                        details["_participants"] = participants
                        yield details
                        count += 1

                except Exception as e:
                    logger.warning(f"Failed to fetch Reactome pathway {pathway_id}: {e}")
                    continue

        logger.info(f"Extracted {count} Reactome pathways total")

    def _fetch_contained_events(self, pathway_id: str) -> List[Dict[str, Any]]:
        """Fetch sub-pathways (contained events) for a top-level pathway."""
        url = f"{self.api_base_url}/data/pathway/{pathway_id}/containedEvents"
        try:
            result = self.fetch_with_retry(url)
            if isinstance(result, list):
                # API may return list of dicts or list of IDs — filter to dicts only
                return [item for item in result if isinstance(item, dict)]
            return []
        except Exception as e:
            logger.warning(f"Failed to fetch contained events for {pathway_id}: {e}")
            return []

    def _fetch_pathway_details(self, pathway_id: str) -> Optional[Dict[str, Any]]:
        """Fetch detailed information for a single pathway."""
        url = f"{self.api_base_url}/data/query/{pathway_id}"
        try:
            result = self.fetch_with_retry(url)
            if isinstance(result, dict):
                return result
            return None
        except Exception as e:
            logger.warning(f"Failed to fetch pathway details for {pathway_id}: {e}")
            return None

    def _fetch_participants(self, pathway_id: str) -> List[Dict[str, Any]]:
        """Fetch molecular participants for a pathway."""
        url = f"{self.api_base_url}/data/participants/{pathway_id}"
        try:
            result = self.fetch_with_retry(url)
            if isinstance(result, list):
                return result
            return []
        except Exception as e:
            logger.warning(f"Failed to fetch participants for {pathway_id}: {e}")
            return []

    def _filter_relevant_pathways(self, pathways: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filter pathways to those relevant to microbiome research."""
        relevant = []
        for pathway in pathways:
            name = (pathway.get("displayName") or pathway.get("name") or "").lower()
            if any(keyword in name for keyword in MICROBIOME_PATHWAY_KEYWORDS):
                relevant.append(pathway)
        return relevant

    def _extract_chebi_ids(self, participants: List[Dict[str, Any]]) -> List[str]:
        """Extract ChEBI identifiers from pathway participants."""
        chebi_ids = []
        for participant in participants:
            # Reactome participants can have cross-references
            xrefs = participant.get("crossReference") or []
            if isinstance(xrefs, list):
                for xref in xrefs:
                    db_name = (xref.get("databaseName") or "").lower()
                    identifier = xref.get("identifier") or ""
                    if db_name == "chebi" and identifier:
                        chebi_ids.append(identifier)

            # Also check referenceEntity for ChEBI
            ref_entity = participant.get("referenceEntity")
            if isinstance(ref_entity, dict):
                ref_db = (ref_entity.get("databaseName") or "").lower()
                ref_id = ref_entity.get("identifier") or ""
                if ref_db == "chebi" and ref_id:
                    chebi_ids.append(ref_id)

        return list(set(chebi_ids))

    def _extract_protein_refs(
        self, participants: List[Dict[str, Any]]
    ) -> List[Dict[str, Optional[str]]]:
        """Extract (gene_name, uniprot_id) pairs from UniProt-referenced participants.

        Returns dicts with keys 'gene_name' and 'uniprot_id'. Either may be None
        when the source entry is missing one. Deduplicated.
        """
        refs: List[Dict[str, Optional[str]]] = []
        seen: set = set()
        for participant in participants:
            ref_entity = participant.get("referenceEntity")
            if not isinstance(ref_entity, dict):
                continue
            db_name = (ref_entity.get("databaseName") or "").lower()
            if db_name != "uniprot":
                continue
            uniprot_id = ref_entity.get("identifier") or None
            gene_name_field = ref_entity.get("geneName")
            gene_names: List[str] = []
            if isinstance(gene_name_field, list):
                gene_names = [g for g in gene_name_field if isinstance(g, str) and g]
            elif isinstance(gene_name_field, str) and gene_name_field:
                gene_names = [gene_name_field]
            for g in gene_names or [None]:
                key = (g, uniprot_id)
                if key in seen or (g is None and uniprot_id is None):
                    continue
                seen.add(key)
                refs.append({"gene_name": g, "uniprot_id": uniprot_id})
        return refs

    def _extract_gene_names(self, participants: List[Dict[str, Any]]) -> List[str]:
        """Backward-compat helper. Returns just unique gene names."""
        return list({r["gene_name"] for r in self._extract_protein_refs(participants) if r["gene_name"]})

    def transform(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Transform a Reactome pathway record for Neo4j loading."""
        pathway_id = record.get("stId")
        if not pathway_id:
            return None

        name = record.get("displayName") or record.get("name")
        if not name:
            return None

        # Determine species
        species_list = record.get("species")
        species = "Homo sapiens"
        if isinstance(species_list, list) and species_list:
            first = species_list[0]
            if isinstance(first, dict):
                species = first.get("displayName") or species

        # Summation text as description
        summation = record.get("summation")
        description = None
        if isinstance(summation, list) and summation:
            first_summary = summation[0]
            if isinstance(first_summary, dict):
                description = first_summary.get("text")
            elif isinstance(first_summary, str):
                description = first_summary

        # Category from extract phase
        category = record.get("_category", "")

        # Determine pathway_type based on category
        category_lower = category.lower()
        if "metabolism" in category_lower:
            pathway_type = "metabolic"
        elif "immune" in category_lower:
            pathway_type = "immune"
        elif "signal" in category_lower:
            pathway_type = "signaling"
        else:
            pathway_type = "other"

        # Extract participant cross-references
        participants = record.get("_participants", [])
        chebi_ids = self._extract_chebi_ids(participants)
        protein_refs = self._extract_protein_refs(participants)
        gene_names = list({r["gene_name"] for r in protein_refs if r["gene_name"]})

        return {
            "pathway_id": f"REACTOME:{pathway_id}",
            "reactome_id": pathway_id,
            "name": name,
            "description": description,
            "category": category,
            "species": species,
            "pathway_type": pathway_type,
            "source": "reactome",
            "organization_id": self.organization_id,

            # For relationship creation (not stored as node properties)
            "_chebi_ids": chebi_ids,
            "_gene_names": gene_names,
            "_protein_refs": protein_refs,
        }

    def load_batch(self, batch: List[Dict[str, Any]]) -> Dict[str, int]:
        """Load a batch of Reactome pathways into Neo4j."""
        if not batch:
            return {"nodes_created": 0, "relationships_created": 0}

        nodes_created = 0
        rels_created = 0

        # Load pathway nodes
        nodes_created += self._load_pathways(batch)

        # Load metabolite relationships (via ChEBI cross-reference)
        for record in batch:
            pathway_id = record["pathway_id"]
            for chebi_id in record.get("_chebi_ids", []):
                rels_created += self._link_metabolite(pathway_id, chebi_id)

        # Load gene/protein relationships. Prefer _protein_refs (with UniProt
        # IDs) when present; fall back to _gene_names for older transformed
        # records.
        for record in batch:
            pathway_id = record["pathway_id"]
            protein_refs = record.get("_protein_refs")
            if protein_refs:
                for ref in protein_refs:
                    rels_created += self._link_gene(
                        pathway_id,
                        ref.get("gene_name") or "",
                        uniprot_id=ref.get("uniprot_id"),
                    )
            else:
                for gene_name in record.get("_gene_names", []):
                    rels_created += self._link_gene(pathway_id, gene_name)

        return {
            "nodes_created": nodes_created,
            "relationships_created": rels_created,
        }

    def _load_pathways(self, batch: List[Dict[str, Any]]) -> int:
        """Load Pathway nodes using batch UNWIND."""
        pathway_records = []
        for record in batch:
            p = {k: v for k, v in record.items() if not k.startswith("_")}
            pathway_records.append(p)

        query = """
            UNWIND $pathways AS p
            MERGE (pathway:Pathway {pathway_id: p.pathway_id})
            ON CREATE SET
                pathway.reactome_id = p.reactome_id,
                pathway.name = p.name,
                pathway.description = p.description,
                pathway.category = p.category,
                pathway.species = p.species,
                pathway.pathway_type = p.pathway_type,
                pathway.source = p.source,
                pathway.organization_id = p.organization_id,
                pathway.created_at = datetime()
            ON MATCH SET
                pathway.name = p.name,
                pathway.description = p.description,
                pathway.category = p.category,
                pathway.species = p.species,
                pathway.pathway_type = p.pathway_type,
                pathway.source = p.source,
                pathway.updated_at = datetime()
            RETURN count(pathway) AS count
        """

        result = self.execute_cypher(query, {"pathways": pathway_records})
        return result[0]["count"] if result else 0

    def _link_metabolite(self, pathway_id: str, chebi_id: str) -> int:
        """Link an existing Metabolite node to a Pathway via ChEBI cross-reference."""
        # Try matching by chebi_id property or by metabolite_id with CHEBI: prefix
        query = """
            MATCH (p:Pathway {pathway_id: $pathway_id})
            MATCH (m:Compound)
            WHERE m.chebi_id = $chebi_id OR m.compound_id = $metabolite_id OR m.metabolite_id = $metabolite_id
            MERGE (m)-[r:PARTICIPATES_IN]->(p)
            ON CREATE SET
                r.evidence_source = 'Reactome',
                r.chebi_id = $chebi_id,
                r.created_at = datetime()
            RETURN count(r) AS count
        """

        result = self.execute_cypher(query, {
            "pathway_id": pathway_id,
            "chebi_id": chebi_id,
            "metabolite_id": f"CHEBI:{chebi_id}",
        })

        return result[0]["count"] if result else 0

    def _link_gene(
        self,
        pathway_id: str,
        gene_name: str,
        uniprot_id: Optional[str] = None,
    ) -> int:
        """Link an existing Protein or Gene node to a Pathway.

        Resolution order:
        1. Protein matched by uniprot_id (when provided) — covers DrugBank's
           ``protein_id = "UniProt:..."`` nodes via uniprot_id / uniprot_accession.
        2. Protein matched by gene_name property.
        3. Gene matched by name.

        Whichever node is found first via COALESCE gets the PARTICIPATES_IN
        edge. Both lookups remain OPTIONAL so behavior degrades gracefully
        when neither side has the data.
        """
        query = """
            MATCH (p:Pathway {pathway_id: $pathway_id})
            OPTIONAL MATCH (pr_u:Protein)
                WHERE $uniprot_id IS NOT NULL
                  AND (pr_u.uniprot_id = $uniprot_id
                       OR pr_u.uniprot_accession = $uniprot_id
                       OR pr_u.protein_id = 'UniProt:' + $uniprot_id)
            OPTIONAL MATCH (pr_g:Protein {gene_name: $gene_name})
            OPTIONAL MATCH (g:Gene {name: $gene_name})
            WITH p, COALESCE(pr_u, pr_g, g) AS entity
            WHERE entity IS NOT NULL
            MERGE (entity)-[r:PARTICIPATES_IN]->(p)
            ON CREATE SET
                r.evidence_source = 'Reactome',
                r.created_at = datetime()
            RETURN count(r) AS count
        """

        result = self.execute_cypher(query, {
            "pathway_id": pathway_id,
            "gene_name": gene_name,
            "uniprot_id": uniprot_id,
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
        loader = ReactomeLoader(
            driver=driver,
            organization_id="default",
        )

        stats = loader.run()
        print(f"Loaded Reactome data: {stats.to_dict()}")
    finally:
        driver.close()
