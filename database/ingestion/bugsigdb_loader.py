"""
BugSigDB Data Loader

Loads microbiome signature data from BugSigDB into Neo4j.
BugSigDB contains community-curated microbial signatures of differential
abundance studies, where each signature can contain multiple taxa.

Data source: https://bugsigdb.org
"""

from typing import Iterator, Dict, Any, List
from .base_loader import FileBasedLoader, LoaderStats, normalize_disease_name, generate_disease_id
from datetime import datetime, timezone
import csv
import logging

logger = logging.getLogger(__name__)

# Map raw direction labels to normalized values
DIRECTION_MAP = {
    "increased": "enriched",
    "enriched": "enriched",
    "UP": "enriched",
    "decreased": "depleted",
    "depleted": "depleted",
    "DOWN": "depleted",
}


class BugSigDBLoader(FileBasedLoader):
    """
    Load BugSigDB microbiome signatures into Neo4j.

    BugSigDB signatures differ from other sources in that each row/record
    contains MULTIPLE taxa (semicolon-separated). The transform() method
    is therefore a generator that yields one dict per taxon.
    """

    @property
    def source_name(self) -> str:
        return "BugSigDB"

    def __init__(
        self,
        driver,
        organization_id: str,
        file_path: str,
        batch_size: int = 500,
        database: str = "neo4j",
    ):
        """
        Initialize the BugSigDB loader.

        Args:
            driver: Neo4j driver
            organization_id: Organization ID for multi-tenant isolation
            file_path: Path to BugSigDB export TSV/CSV file
            batch_size: Records per batch
            database: Neo4j database name
        """
        super().__init__(driver, organization_id, file_path, batch_size, database)

    def extract(self, **kwargs) -> Iterator[Dict[str, Any]]:
        """
        Extract data from a BugSigDB TSV/CSV export.

        Expected columns include:
        - Condition: disease or condition name
        - Microbe Names: semicolon-separated taxon names
        - NCBI Taxonomy IDs: semicolon-separated NCBI IDs
        - Direction: increased/decreased/UP/DOWN etc.
        - Study: study name or title
        - PMID: PubMed ID
        - Body Site: body site of the sample
        - Sequencing Type: sequencing methodology
        - Sample Size: number of samples
        """
        logger.info(f"Loading BugSigDB data from {self.file_path}")

        with open(self.file_path, "r", encoding="utf-8") as f:
            # Skip comment lines (GitHub export starts with # lines)
            lines = [line for line in f if not line.startswith("#")]

        import io
        text = io.StringIO("".join(lines))
        # Detect delimiter from header
        delimiter = "\t" if "\t" in lines[0] else ","

        reader = csv.DictReader(text, delimiter=delimiter)
        for row in reader:
            yield row

    def transform(self, record: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
        """
        Transform a BugSigDB record into one or more graph-ready dicts.

        Each BugSigDB row may list multiple taxa separated by semicolons.
        This method is a generator that yields one dict per taxon.

        Args:
            record: Raw row from the BugSigDB export

        Yields:
            One transformed dict per taxon in the signature
        """
        condition = (record.get("Condition") or "").strip()
        # Support both wiki export ("Microbe Names") and GitHub export ("MetaPhlAn taxon names")
        microbe_names = (record.get("Microbe Names") or record.get("MetaPhlAn taxon names") or "").strip()

        if not condition or not microbe_names:
            return

        names = [n.strip() for n in microbe_names.split(";") if n.strip()]
        raw_ids = (record.get("NCBI Taxonomy IDs") or "").strip()
        ids = [i.strip() for i in raw_ids.split(";")] if raw_ids else []

        # Pad IDs list if shorter than names
        while len(ids) < len(names):
            ids.append("")

        # Support both "Direction" and "Abundance in Group 1" columns
        raw_direction = (record.get("Direction") or record.get("Abundance in Group 1") or "").strip()
        direction = DIRECTION_MAP.get(raw_direction.lower(), "altered")

        disease_name_normalized = normalize_disease_name(condition)
        disease_id = generate_disease_id(condition)

        study_name = (record.get("Study") or "").strip()
        study_pmid = (record.get("PMID") or "").strip()
        body_site = (record.get("Body Site") or "").strip()
        sequencing_type = (record.get("Sequencing Type") or "").strip()
        sample_size = (record.get("Sample Size") or "").strip()

        # `ids` is padded to `len(names)` above when shorter; we intentionally
        # ignore any trailing IDs if it's longer (silent-truncate semantics).
        for name, ncbi_id in zip(names, ids, strict=False):
            # Parse MetaPhlAn lineage format: "k__X|p__Y|...|s__Species"
            if "|" in name:
                parts = name.split("|")
                # Take the most specific (last) taxon level
                last_part = parts[-1].strip()
                # Remove rank prefix like "s__", "g__"
                if "__" in last_part:
                    name = last_part.split("__", 1)[1].replace("_", " ")
                else:
                    name = last_part

            # Truncate names that are still too long for Neo4j index (max ~8KB)
            if len(name) > 500:
                name = name[:500]

            if ncbi_id:
                taxon_id = f"NCBITaxon:{ncbi_id}"
            else:
                taxon_id = f"bugsigdb:{name.replace(' ', '_').lower()}"

            yield {
                "taxon_name": name,
                "taxon_id": taxon_id,
                "ncbi_tax_id": ncbi_id,
                "disease_name": condition,
                "disease_name_normalized": disease_name_normalized,
                "disease_id": disease_id,
                "direction": direction,
                "study_name": study_name,
                "study_pmid": study_pmid,
                "body_site": body_site,
                "sequencing_type": sequencing_type,
                "sample_size": sample_size,
                "source": "BugSigDB",
                "organization_id": self.organization_id,
            }

    def run(self, **extract_kwargs) -> LoaderStats:
        """
        Run the ETL pipeline, handling the generator-based transform.

        Unlike the base class run(), this iterates the generator returned
        by transform() so that multi-taxon rows are correctly expanded.
        """
        logger.info(f"Starting {self.source_name} data ingestion for org {self.organization_id}")
        self.stats = LoaderStats(source=self.source_name)

        try:
            batch: List[Dict[str, Any]] = []
            total_processed = 0

            for raw_record in self.extract(**extract_kwargs):
                try:
                    for transformed in self.transform(raw_record):
                        batch.append(transformed)

                        if len(batch) >= self.batch_size:
                            self._process_batch(batch)
                            total_processed += len(batch)
                            batch = []

                            if total_processed % 10000 == 0:
                                logger.info(f"Processed {total_processed} records...")
                except Exception as e:
                    self.stats.errors.append(f"Transform error: {str(e)}")
                    logger.warning(f"Transform error: {e}")

            # Process remaining batch
            if batch:
                self._process_batch(batch)
                total_processed += len(batch)

            self.stats.completed_at = datetime.now(timezone.utc)
            logger.info(f"Completed {self.source_name} ingestion: {self.stats.to_dict()}")

        except Exception as e:
            self.stats.errors.append(f"Pipeline error: {str(e)}")
            logger.exception("Pipeline error: %s", e)
            raise

        return self.stats

    def load_batch(self, batch: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Load a batch of transformed BugSigDB records into Neo4j.

        Creates:
        - Taxon nodes (MERGE by taxon_id)
        - Disease nodes (MERGE by name_normalized)
        - Study nodes (MERGE by study_id = PMID:{pmid})
        - ASSOCIATED_WITH_DISEASE relationships with pmid tracking
        """
        if not batch:
            return {"nodes_created": 0, "relationships_created": 0}

        nodes_created = 0
        rels_created = 0

        # --- Taxon nodes ---
        taxa = {}
        for r in batch:
            tid = r["taxon_id"]
            if tid not in taxa:
                taxa[tid] = {
                    "taxon_id": tid,
                    "name": r["taxon_name"],
                    "ncbi_tax_id": r.get("ncbi_tax_id"),
                    "organization_id": self.organization_id,
                }

        query = """
            UNWIND $taxa AS t
            MERGE (taxon:Taxon {taxon_id: t.taxon_id})
            ON CREATE SET
                taxon.name = t.name,
                taxon.ncbi_tax_id = t.ncbi_tax_id,
                taxon.organization_id = t.organization_id,
                taxon.created_at = datetime()
            ON MATCH SET
                taxon.name = COALESCE(taxon.name, t.name),
                taxon.ncbi_tax_id = COALESCE(taxon.ncbi_tax_id, t.ncbi_tax_id),
                taxon.updated_at = datetime()
            RETURN count(taxon) AS count
        """
        result = self.execute_cypher(query, {"taxa": list(taxa.values())})
        nodes_created += result[0]["count"] if result else 0

        # --- Disease nodes ---
        diseases = {}
        for r in batch:
            norm = r["disease_name_normalized"]
            if norm and norm not in diseases:
                diseases[norm] = {
                    "name_normalized": norm,
                    "name": r["disease_name"],
                    "disease_id": r["disease_id"],
                    "microbiome_associated": True,
                    "organization_id": self.organization_id,
                    "source": "BugSigDB",
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

        # --- Study nodes ---
        studies = {}
        for r in batch:
            pmid = r.get("study_pmid")
            if pmid:
                study_id = f"PMID:{pmid}"
                if study_id not in studies:
                    studies[study_id] = {
                        "study_id": study_id,
                        "pmid": pmid,
                        "name": r.get("study_name", ""),
                        "body_site": r.get("body_site", ""),
                        "sequencing_type": r.get("sequencing_type", ""),
                        "sample_size": r.get("sample_size", ""),
                        "source": "BugSigDB",
                        "organization_id": self.organization_id,
                    }

        if studies:
            query = """
                UNWIND $studies AS s
                MERGE (study:Study {study_id: s.study_id})
                ON CREATE SET
                    study.pmid = s.pmid,
                    study.name = s.name,
                    study.body_site = s.body_site,
                    study.sequencing_type = s.sequencing_type,
                    study.sample_size = s.sample_size,
                    study.source = s.source,
                    study.organization_id = s.organization_id,
                    study.created_at = datetime()
                ON MATCH SET
                    study.updated_at = datetime()
                RETURN count(study) AS count
            """
            result = self.execute_cypher(query, {"studies": list(studies.values())})
            nodes_created += result[0]["count"] if result else 0

        # --- ASSOCIATED_WITH_DISEASE relationships ---
        associations = []
        for r in batch:
            associations.append({
                "taxon_id": r["taxon_id"],
                "disease_name_normalized": r["disease_name_normalized"],
                "direction": r["direction"],
                "source": r["source"],
                "pmid": r.get("study_pmid", ""),
            })

        query = """
            UNWIND $associations AS a
            MATCH (taxon:Taxon {taxon_id: a.taxon_id})
            MATCH (disease:Disease {name_normalized: a.disease_name_normalized})
            MERGE (taxon)-[r:ASSOCIATED_WITH_DISEASE]->(disease)
            ON CREATE SET
                r.direction = a.direction,
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
