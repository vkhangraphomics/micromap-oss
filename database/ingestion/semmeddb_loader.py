"""
SemMedDB (Semantic MEDLINE) Data Loader

Loads pre-extracted subject-predicate-object triples from SemMedDB into Neo4j.
SemMedDB contains semantic predications (subject-predicate-object triples)
extracted from biomedical literature titles and abstracts by SemRep.

Data source: https://lhncbc.nlm.nih.gov/ii/tools/SemRep_SemMedDB_SKR.html
"""

from typing import Optional, Iterator, Dict, Any, List
from .base_loader import FileBasedLoader, normalize_disease_name, generate_disease_id
import csv
import logging

logger = logging.getLogger(__name__)

# UMLS semantic types for microbiome-related entities
MICROBIOME_SEMTYPES = {"bact", "fngs", "virs", "arch"}

# UMLS semantic types for disease-related entities
DISEASE_SEMTYPES = {"dsyn", "neop", "mobd", "patf", "sosy"}

# SemMedDB predicates relevant to microbiome-disease associations
RELEVANT_PREDICATES = {
    "CAUSES", "TREATS", "PREVENTS", "PREDISPOSES",
    "ASSOCIATED_WITH", "AFFECTS", "INHIBITS", "STIMULATES",
    "PRODUCES", "INTERACTS_WITH", "DISRUPTS", "AUGMENTS",
    "COMPLICATES", "COEXISTS_WITH",
}

# Map SemMedDB predicates to microbiome association direction
PREDICATE_TO_DIRECTION = {
    "CAUSES": "enriched",
    "PREDISPOSES": "enriched",
    "COMPLICATES": "enriched",
    "TREATS": "depleted",
    "PREVENTS": "depleted",
    "INHIBITS": "depleted",
}


class SemMedDBLoader(FileBasedLoader):
    """
    Load SemMedDB semantic predications into Neo4j as microbiome-disease
    associations.

    Filters triples to those where:
    - Subject is a microbiome-related entity (bacteria, fungi, virus, archaea)
    - Object is a disease-related entity
    - Predicate is in the relevant set

    Expected CSV/TSV columns:
    - SUBJECT_CUI: UMLS CUI for subject
    - SUBJECT_NAME: Subject concept name
    - SUBJECT_SEMTYPE: Subject semantic type abbreviation
    - PREDICATE: Predicate (e.g., CAUSES, TREATS)
    - OBJECT_CUI: UMLS CUI for object
    - OBJECT_NAME: Object concept name
    - OBJECT_SEMTYPE: Object semantic type abbreviation
    - PMID: PubMed ID of source article
    - SENTENCE: Source sentence (optional)
    """

    def __init__(
        self,
        driver,
        organization_id: str,
        file_path: str,
        batch_size: int = 1000,
        database: str = "neo4j",
        min_predication_count: int = 1,
    ):
        """
        Initialize the SemMedDB loader.

        Args:
            driver: Neo4j driver
            organization_id: Organization ID for multi-tenant isolation
            file_path: Path to SemMedDB predications CSV/TSV file
            batch_size: Records per batch
            database: Neo4j database name
            min_predication_count: Minimum number of predications required
                to include an association (for filtering noise)
        """
        super().__init__(driver, organization_id, file_path, batch_size, database)
        self.min_predication_count = min_predication_count

    @property
    def source_name(self) -> str:
        return "SemMedDB"

    def extract(self, **kwargs) -> Iterator[Dict[str, Any]]:
        """
        Extract predication triples from SemMedDB CSV/TSV export.

        Yields:
            Dictionary records with subject, predicate, object fields
        """
        logger.info(f"Loading SemMedDB predications from {self.file_path}")

        with open(self.file_path, "r", encoding="utf-8") as f:
            # Auto-detect delimiter
            sample = f.read(4096)
            f.seek(0)
            if "\t" in sample and sample.count("\t") > sample.count(","):
                delimiter = "\t"
            else:
                delimiter = ","

            reader = csv.DictReader(f, delimiter=delimiter)
            for row in reader:
                yield row

    def transform(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Transform a SemMedDB predication triple to graph format.

        Filters for microbiome-subject → disease-object triples with
        relevant predicates.

        Args:
            record: Raw predication record from SemMedDB

        Returns:
            Transformed record for Neo4j, or None if filtered out
        """
        subject_semtype = (record.get("SUBJECT_SEMTYPE") or "").strip().lower()
        object_semtype = (record.get("OBJECT_SEMTYPE") or "").strip().lower()
        predicate = (record.get("PREDICATE") or "").strip().upper()

        # Filter: subject must be a microbiome-related entity
        if subject_semtype not in MICROBIOME_SEMTYPES:
            return None

        # Filter: object must be a disease-related entity
        if object_semtype not in DISEASE_SEMTYPES:
            return None

        # Filter: predicate must be relevant
        if predicate not in RELEVANT_PREDICATES:
            return None

        subject_name = (record.get("SUBJECT_NAME") or "").strip()
        object_name = (record.get("OBJECT_NAME") or "").strip()

        # Skip records with missing names
        if not subject_name or not object_name:
            return None

        # Generate taxon ID from UMLS CUI
        subject_cui = (record.get("SUBJECT_CUI") or "").strip()
        if subject_cui:
            taxon_id = f"UMLS:{subject_cui}"
        else:
            taxon_id = f"semmeddb:{subject_name.replace(' ', '_').lower()}"

        # Normalize disease name and generate disease ID
        disease_name_normalized = normalize_disease_name(object_name)
        object_cui = (record.get("OBJECT_CUI") or "").strip()
        disease_id = generate_disease_id(
            object_name,
            {"umls_cui": object_cui} if object_cui else None,
        )

        # Map predicate to association direction
        direction = PREDICATE_TO_DIRECTION.get(predicate, "altered")

        pmid = (record.get("PMID") or "").strip()
        sentence = (record.get("SENTENCE") or "").strip()

        return {
            # Taxon info
            "taxon_id": taxon_id,
            "taxon_name": subject_name,
            "umls_cui_subject": subject_cui,
            "subject_semtype": subject_semtype,
            # Disease info
            "disease_id": disease_id,
            "disease_name": object_name,
            "disease_name_normalized": disease_name_normalized,
            "umls_cui_object": object_cui,
            "object_semtype": object_semtype,
            # Association info
            "predicate": predicate,
            "direction": direction,
            "pmid": pmid,
            "sentence": sentence,
            "source": "SemMedDB",
            # Multi-tenant
            "organization_id": self.organization_id,
        }

    def load_batch(self, batch: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Load a batch of SemMedDB predications into Neo4j.

        Creates:
        - Taxon nodes (MERGE by taxon_id, with umls_cui property)
        - Disease nodes (MERGE by name_normalized, with umls_cui property)
        - ASSOCIATED_WITH_DISEASE relationships with predicate metadata
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
                    "umls_cui": record.get("umls_cui_subject"),
                    "organization_id": self.organization_id,
                }

        query = """
            UNWIND $taxa AS t
            MERGE (taxon:Taxon {taxon_id: t.taxon_id})
            ON CREATE SET
                taxon.name = t.name,
                taxon.umls_cui = t.umls_cui,
                taxon.organization_id = t.organization_id,
                taxon.created_at = datetime()
            ON MATCH SET
                taxon.name = COALESCE(taxon.name, t.name),
                taxon.umls_cui = COALESCE(taxon.umls_cui, t.umls_cui),
                taxon.updated_at = datetime()
            RETURN count(taxon) AS count
        """

        result = self.execute_cypher(query, {"taxa": list(taxa.values())})
        return result[0]["count"] if result else 0

    def _load_diseases(self, batch: List[Dict[str, Any]]) -> int:
        """Load Disease nodes from batch using normalized name as MERGE key."""
        diseases = {}
        for record in batch:
            norm_name = record["disease_name_normalized"]
            if norm_name and norm_name not in diseases:
                diseases[norm_name] = {
                    "name_normalized": norm_name,
                    "name": record["disease_name"],
                    "disease_id": record["disease_id"],
                    "umls_cui": record.get("umls_cui_object"),
                    "microbiome_associated": True,
                    "organization_id": self.organization_id,
                    "source": "SemMedDB",
                }

        query = """
            UNWIND $diseases AS d
            MERGE (disease:Disease {name_normalized: d.name_normalized})
            ON CREATE SET
                disease.name = d.name,
                disease.disease_id = d.disease_id,
                disease.umls_cui = d.umls_cui,
                disease.microbiome_associated = d.microbiome_associated,
                disease.organization_id = d.organization_id,
                disease.sources = [d.source],
                disease.created_at = datetime()
            ON MATCH SET
                disease.umls_cui = COALESCE(disease.umls_cui, d.umls_cui),
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
        """Load microbiome-disease associations with predicate metadata."""
        associations = []
        for record in batch:
            associations.append({
                "taxon_id": record["taxon_id"],
                "disease_name_normalized": record["disease_name_normalized"],
                "predicate": record["predicate"],
                "direction": record["direction"],
                "pmid": record["pmid"],
                "sentence": record["sentence"],
                "source": record["source"],
            })

        query = """
            UNWIND $associations AS a
            MATCH (taxon:Taxon {taxon_id: a.taxon_id})
            MATCH (disease:Disease {name_normalized: a.disease_name_normalized})
            MERGE (taxon)-[r:ASSOCIATED_WITH_DISEASE]->(disease)
            ON CREATE SET
                r.predicate = a.predicate,
                r.direction = a.direction,
                r.evidence_level = 'text_mined',
                r.sources = [a.source],
                r.pmids = CASE WHEN a.pmid IS NOT NULL AND a.pmid <> '' THEN [a.pmid] ELSE [] END,
                r.sentences = CASE WHEN a.sentence IS NOT NULL AND a.sentence <> '' THEN [a.sentence] ELSE [] END,
                r.predicates = [a.predicate],
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
                r.sentences = CASE
                    WHEN a.sentence IS NOT NULL AND a.sentence <> '' AND NOT a.sentence IN r.sentences THEN r.sentences + a.sentence
                    ELSE r.sentences
                END,
                r.predicates = CASE
                    WHEN NOT a.predicate IN r.predicates THEN r.predicates + a.predicate
                    ELSE r.predicates
                END,
                r.n_studies = size(r.pmids),
                r.updated_at = datetime()
            RETURN count(r) AS count
        """

        result = self.execute_cypher(query, {"associations": associations})
        return result[0]["count"] if result else 0
