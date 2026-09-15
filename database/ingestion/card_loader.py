"""
CARD (Comprehensive Antibiotic Resistance Database) Loader

Loads antibiotic-bacteria relationships from CARD database:
- Creates Drug nodes for antibiotics
- Creates (Drug)-[:EFFECTIVE_AGAINST]->(Taxon) relationships
- Links drug classes to antibiotics

CARD Data Source: https://card.mcmaster.ca/download
"""

import os
import json
import logging
from typing import Dict, List, Any, Iterator, Optional, Set
from neo4j import Driver

from .base_loader import FileBasedLoader

logger = logging.getLogger(__name__)


# Common antibiotics mapped to known bacteria they target
# This supplements the CARD data with well-established clinical relationships
ANTIBIOTIC_TARGETS = {
    # Gram-positive targeting
    "vancomycin": ["Staphylococcus", "Streptococcus", "Enterococcus", "Clostridium"],
    "linezolid": ["Staphylococcus", "Streptococcus", "Enterococcus"],
    "daptomycin": ["Staphylococcus", "Streptococcus", "Enterococcus"],

    # Broad-spectrum
    "amoxicillin": ["Streptococcus", "Escherichia", "Haemophilus", "Helicobacter"],
    "ampicillin": ["Streptococcus", "Enterococcus", "Listeria", "Escherichia"],
    "ciprofloxacin": ["Escherichia", "Salmonella", "Shigella", "Pseudomonas", "Campylobacter"],
    "levofloxacin": ["Streptococcus", "Haemophilus", "Pseudomonas", "Escherichia"],

    # Anaerobe targeting
    "metronidazole": ["Bacteroides", "Clostridium", "Clostridioides", "Fusobacterium", "Prevotella"],
    "clindamycin": ["Bacteroides", "Clostridium", "Streptococcus", "Staphylococcus"],

    # Gut microbiome modulators
    "rifaximin": ["Escherichia", "Bacteroides", "Clostridium"],
    "neomycin": ["Escherichia", "Klebsiella", "Enterobacter"],

    # Anti-H. pylori
    "clarithromycin": ["Helicobacter", "Mycobacterium", "Streptococcus"],

    # Clostridioides difficile treatment
    "fidaxomicin": ["Clostridioides"],
}


class CARDLoader(FileBasedLoader):
    """
    Loader for CARD (Comprehensive Antibiotic Resistance Database).

    Creates relationships between drugs/antibiotics and bacteria they target.

    Schema:
    - (Drug)-[:EFFECTIVE_AGAINST {mechanism, resistance_genes}]->(Taxon)
    - (Drug)-[:BELONGS_TO_CLASS]->(DrugClass)
    """

    def __init__(
        self,
        driver: Driver,
        organization_id: str,
        card_data_dir: str = None,
        batch_size: int = 500,
        database: str = "neo4j"
    ):
        """
        Initialize the CARD loader.

        Args:
            driver: Neo4j driver instance
            organization_id: Organization ID for multi-tenant isolation
            card_data_dir: Directory containing CARD data files (card.json, etc.)
            batch_size: Number of records to process per batch
            database: Neo4j database name
        """
        if card_data_dir is None:
            card_data_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "data", "card"
            )

        card_json_path = os.path.join(card_data_dir, "card.json")
        super().__init__(driver, organization_id, card_json_path, batch_size, database)

        self.card_data_dir = card_data_dir
        self.pathogens_file = os.path.join(card_data_dir, "shortname_pathogens.tsv")
        self.antibiotics_file = os.path.join(card_data_dir, "shortname_antibiotics.tsv")

        # Cache for pathogen abbreviation -> species name mapping
        self._pathogen_map: Dict[str, str] = {}
        # Cache for antibiotic abbreviation -> full name mapping
        self._antibiotic_map: Dict[str, str] = {}

    @property
    def source_name(self) -> str:
        return "CARD"

    def _load_pathogen_mappings(self) -> None:
        """Load pathogen abbreviation to species name mapping."""
        if os.path.exists(self.pathogens_file):
            with open(self.pathogens_file, 'r') as f:
                next(f)  # Skip header
                for line in f:
                    parts = line.strip().split('\t')
                    if len(parts) >= 2:
                        abbrev, pathogen = parts[0], parts[1]
                        self._pathogen_map[abbrev] = pathogen
                        # Also extract genus from species name
                        genus = pathogen.split()[0]
                        self._pathogen_map[f"genus:{genus.lower()}"] = genus
        logger.info(f"Loaded {len(self._pathogen_map)} pathogen mappings")

    def _load_antibiotic_mappings(self) -> None:
        """Load antibiotic abbreviation mappings."""
        if os.path.exists(self.antibiotics_file):
            with open(self.antibiotics_file, 'r') as f:
                next(f)  # Skip header
                for line in f:
                    parts = line.strip().split('\t')
                    if len(parts) >= 2:
                        abbrev, antibiotic = parts[0], parts[1]
                        self._antibiotic_map[abbrev] = antibiotic
        logger.info(f"Loaded {len(self._antibiotic_map)} antibiotic mappings")

    def _extract_from_card_json(self) -> Iterator[Dict[str, Any]]:
        """Extract antibiotic-bacteria relationships from card.json."""
        with open(self.file_path, 'r') as f:
            card_data = json.load(f)

        for entry_id, entry in card_data.items():
            if not isinstance(entry, dict):
                continue

            # Extract drug classes and antibiotics from ARO categories
            aro_cat = entry.get('ARO_category', {})
            if not isinstance(aro_cat, dict):
                continue

            drug_classes: Set[str] = set()
            antibiotics: Set[str] = set()
            amr_gene_family = None
            resistance_mechanism = None

            for cat_id, cat_info in aro_cat.items():
                if not isinstance(cat_info, dict):
                    continue

                class_name = cat_info.get('category_aro_class_name', '')
                aro_name = cat_info.get('category_aro_name', '')

                if class_name == 'Drug Class':
                    drug_classes.add(aro_name)
                elif class_name == 'Antibiotic':
                    antibiotics.add(aro_name)
                elif class_name == 'AMR Gene Family':
                    amr_gene_family = aro_name
                elif class_name == 'Resistance Mechanism':
                    resistance_mechanism = aro_name

            # Extract model sequences which may contain species info
            model_sequences = entry.get('model_sequences', {})
            species_found: Set[str] = set()

            if isinstance(model_sequences, dict):
                for seq_id, seq_data in model_sequences.items():
                    if isinstance(seq_data, dict):
                        # NCBI taxonomy info
                        ncbi_tax = seq_data.get('NCBI_taxonomy', {})
                        if isinstance(ncbi_tax, dict):
                            org_name = ncbi_tax.get('NCBI_taxonomy_name', '')
                            if org_name:
                                # Extract genus from full species name
                                genus = org_name.split()[0]
                                species_found.add(genus)

            # Yield record if we have antibiotic info
            if antibiotics or drug_classes:
                yield {
                    'entry_id': entry_id,
                    'model_name': entry.get('model_name', ''),
                    'aro_name': entry.get('ARO_name', ''),
                    'aro_accession': entry.get('ARO_accession', ''),
                    'antibiotics': list(antibiotics),
                    'drug_classes': list(drug_classes),
                    'amr_gene_family': amr_gene_family,
                    'resistance_mechanism': resistance_mechanism,
                    'species': list(species_found)
                }

    def extract(self, **kwargs) -> Iterator[Dict[str, Any]]:
        """
        Extract antibiotic-bacteria relationships from CARD data.
        """
        self._load_pathogen_mappings()
        self._load_antibiotic_mappings()

        # First yield records from CARD JSON
        yield from self._extract_from_card_json()

        # Also yield curated antibiotic-target relationships
        for antibiotic, targets in ANTIBIOTIC_TARGETS.items():
            yield {
                'source': 'curated',
                'antibiotics': [antibiotic],
                'drug_classes': [],
                'target_genera': targets,
                'amr_gene_family': None,
                'resistance_mechanism': 'antibacterial activity'
            }

    def transform(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Transform CARD record to graph-ready format."""
        if record.get('source') == 'curated':
            # Curated record - direct antibiotic-target mapping
            return {
                'type': 'curated_target',
                'antibiotic': record['antibiotics'][0].lower(),
                'antibiotic_display': record['antibiotics'][0].title(),
                'target_genera': record['target_genera'],
                'mechanism': record.get('resistance_mechanism', 'antibacterial activity')
            }

        # CARD record - extract antibiotic and resistance info
        transformed = {
            'type': 'card_resistance',
            'entry_id': record['entry_id'],
            'antibiotics': [a.lower() for a in record.get('antibiotics', [])],
            'drug_classes': record.get('drug_classes', []),
            'amr_gene_family': record.get('amr_gene_family'),
            'resistance_mechanism': record.get('resistance_mechanism'),
            'species': record.get('species', [])
        }

        return transformed

    def load_batch(self, batch: List[Dict[str, Any]]) -> Dict[str, int]:
        """Load a batch of records into Neo4j."""
        nodes_created = 0
        rels_created = 0

        # Separate curated vs CARD records
        curated_records = [r for r in batch if r.get('type') == 'curated_target']
        card_records = [r for r in batch if r.get('type') == 'card_resistance']

        # Process curated antibiotic-target relationships
        if curated_records:
            result = self._load_curated_targets(curated_records)
            nodes_created += result.get('nodes_created', 0)
            rels_created += result.get('rels_created', 0)

        # Process CARD resistance records (create Drug nodes with drug class info)
        if card_records:
            result = self._load_card_records(card_records)
            nodes_created += result.get('nodes_created', 0)
            rels_created += result.get('rels_created', 0)

        return {
            'nodes_created': nodes_created,
            'relationships_created': rels_created
        }

    def _load_curated_targets(self, records: List[Dict[str, Any]]) -> Dict[str, int]:
        """Load curated antibiotic-target relationships."""
        nodes_created = 0
        rels_created = 0

        for record in records:
            antibiotic = record['antibiotic']
            antibiotic_display = record['antibiotic_display']
            target_genera = record['target_genera']
            mechanism = record.get('mechanism', 'antibacterial activity')

            # Create/update Drug node
            drug_query = """
                MERGE (d:Drug {name_lower: $name_lower})
                ON CREATE SET
                    d.name = $name,
                    d.drug_type = 'antibiotic',
                    d.source = 'CARD_curated',
                    d.sources = ['CARD_curated'],
                    d.organization_id = $org_id,
                    d.created_at = datetime()
                ON MATCH SET
                    d.updated_at = datetime()
                RETURN d
            """
            self.execute_cypher(drug_query, {
                'name_lower': antibiotic,
                'name': antibiotic_display,
                'org_id': self.organization_id
            })
            nodes_created += 1

            # Create relationships to existing Taxon nodes (by genus name)
            for genus in target_genera:
                rel_query = """
                    MATCH (d:Drug {name_lower: $antibiotic})
                    MATCH (t:Taxon)
                    WHERE toLower(t.name) = toLower($genus)
                       OR t.name STARTS WITH $genus
                       OR toLower(t.genus) = toLower($genus)
                    MERGE (d)-[r:EFFECTIVE_AGAINST]->(t)
                    ON CREATE SET
                        r.mechanism = $mechanism,
                        r.source = 'curated',
                        r.created_at = datetime()
                    RETURN count(r) as count
                """
                result = self.execute_cypher(rel_query, {
                    'antibiotic': antibiotic,
                    'genus': genus,
                    'mechanism': mechanism
                })
                if result:
                    rels_created += result[0].get('count', 0)

        return {'nodes_created': nodes_created, 'rels_created': rels_created}

    def _load_card_records(self, records: List[Dict[str, Any]]) -> Dict[str, int]:
        """Load CARD resistance records (creates Drug nodes with drug class info)."""
        nodes_created = 0
        rels_created = 0

        # Collect unique antibiotics and drug classes
        antibiotics_seen: Set[str] = set()
        drug_class_rels: List[Dict[str, str]] = []

        for record in records:
            for antibiotic in record.get('antibiotics', []):
                if antibiotic not in antibiotics_seen:
                    antibiotics_seen.add(antibiotic)

                    # Create Drug node for antibiotic
                    drug_query = """
                        MERGE (d:Drug {name_lower: $name_lower})
                        ON CREATE SET
                            d.name = $name,
                            d.drug_type = 'antibiotic',
                            d.source = 'CARD',
                            d.sources = ['CARD'],
                            d.organization_id = $org_id,
                            d.created_at = datetime()
                        ON MATCH SET
                            d.updated_at = datetime()
                        RETURN d
                    """
                    self.execute_cypher(drug_query, {
                        'name_lower': antibiotic,
                        'name': antibiotic.title(),
                        'org_id': self.organization_id
                    })
                    nodes_created += 1

            # Track drug class relationships
            for antibiotic in record.get('antibiotics', []):
                for drug_class in record.get('drug_classes', []):
                    drug_class_rels.append({
                        'antibiotic': antibiotic,
                        'drug_class': drug_class
                    })

            # Create relationships to species found in CARD
            for antibiotic in record.get('antibiotics', []):
                for species_genus in record.get('species', []):
                    rel_query = """
                        MATCH (d:Drug {name_lower: $antibiotic})
                        MATCH (t:Taxon)
                        WHERE t.name STARTS WITH $genus
                           OR toLower(t.genus) = toLower($genus)
                        MERGE (d)-[r:HAS_RESISTANCE_IN]->(t)
                        ON CREATE SET
                            r.mechanism = $mechanism,
                            r.amr_gene_family = $gene_family,
                            r.source = 'CARD',
                            r.created_at = datetime()
                        RETURN count(r) as count
                    """
                    result = self.execute_cypher(rel_query, {
                        'antibiotic': antibiotic,
                        'genus': species_genus,
                        'mechanism': record.get('resistance_mechanism'),
                        'gene_family': record.get('amr_gene_family')
                    })
                    if result:
                        rels_created += result[0].get('count', 0)

        # Create DrugClass nodes and relationships
        drug_classes_seen: Set[str] = set()
        for rel in drug_class_rels:
            drug_class = rel['drug_class']
            if drug_class not in drug_classes_seen:
                drug_classes_seen.add(drug_class)

                # Create DrugClass node
                class_query = """
                    MERGE (dc:DrugClass {name_lower: $name_lower})
                    ON CREATE SET
                        dc.name = $name,
                        dc.source = 'CARD',
                        dc.sources = ['CARD'],
                        dc.organization_id = $org_id,
                        dc.created_at = datetime()
                    RETURN dc
                """
                self.execute_cypher(class_query, {
                    'name_lower': drug_class.lower(),
                    'name': drug_class,
                    'org_id': self.organization_id
                })
                nodes_created += 1

            # Create Drug -> DrugClass relationship
            rel_query = """
                MATCH (d:Drug {name_lower: $antibiotic})
                MATCH (dc:DrugClass {name_lower: $drug_class})
                MERGE (d)-[r:BELONGS_TO_CLASS]->(dc)
                RETURN count(r) as count
            """
            result = self.execute_cypher(rel_query, {
                'antibiotic': rel['antibiotic'],
                'drug_class': drug_class.lower()
            })
            if result:
                rels_created += result[0].get('count', 0)

        return {'nodes_created': nodes_created, 'rels_created': rels_created}


def load_card_data(
    driver: Driver,
    organization_id: str = "default",
    card_data_dir: str = None
) -> Dict[str, Any]:
    """
    Convenience function to load CARD antibiotic-bacteria data.

    Args:
        driver: Neo4j driver instance
        organization_id: Organization ID
        card_data_dir: Path to CARD data directory

    Returns:
        Loading statistics
    """
    loader = CARDLoader(
        driver=driver,
        organization_id=organization_id,
        card_data_dir=card_data_dir
    )

    stats = loader.run()
    return stats.to_dict()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    from neo4j import GraphDatabase

    # Connect to Neo4j
    driver = GraphDatabase.driver(
        os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        auth=(
            os.environ.get("NEO4J_USER", "neo4j"),
            os.environ.get("NEO4J_PASSWORD", "password")
        )
    )

    try:
        print("Loading CARD antibiotic-bacteria data...")
        stats = load_card_data(driver, organization_id="default")
        print("\nCARD Loading Complete!")
        print(f"  Nodes created: {stats['nodes_created']}")
        print(f"  Relationships created: {stats['relationships_created']}")
        print(f"  Duration: {stats['duration_seconds']:.1f} seconds")
    finally:
        driver.close()
