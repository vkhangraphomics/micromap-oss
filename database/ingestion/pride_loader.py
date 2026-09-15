"""
PRIDE Archive proteomics loader.

Parses PRIDE archive JSON files (one per study) and writes:
  Nodes: Study, Sample, Measurement
  Edges: HAS_SAMPLE, HAS_MEASUREMENT, QUANTIFIES, SAMPLE_FROM_TISSUE

Protein nodes must be pre-loaded (e.g. via --uniprot-proteomics). QUANTIFIES
edges are created via MATCH on Protein; if no Protein is found the measurement
is logged and skipped (no MERGE).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Generator

from database.ingestion.base_loader import FileBasedLoader

logger = logging.getLogger(__name__)


class PRIDELoader(FileBasedLoader):
    """Load PRIDE Archive proteomics study data.

    Expects file_path to point at a single JSON file **or** data_dir to be a
    directory of ``*.json`` files (one per study).

    Each JSON file has the shape::

        {
            "accession": "PXD001234",
            "title": "...",
            "samples": [
                {
                    "sample_id": "sample_001",
                    "organism_part": "kidney",
                    "uberon_id": "UBERON:0002113",
                    "proteins": [
                        {"uniprot_accession": "P04637", "abundance": 1452.3},
                        ...
                    ]
                },
                ...
            ]
        }
    """

    source_name = "pride"

    # ------------------------------------------------------------------
    # __init__
    # ------------------------------------------------------------------

    def __init__(
        self,
        driver,
        organization_id: str,
        file_path: str | None = None,
        data_dir: str | None = None,
        batch_size: int = 1000,
        database: str = "neo4j",
    ):
        # FileBasedLoader expects a non-None file_path; pass empty string as
        # sentinel when we are using directory-based scanning.
        super().__init__(
            driver=driver,
            organization_id=organization_id,
            file_path=file_path or "",
            batch_size=batch_size,
            database=database,
        )
        self.data_dir = data_dir or ""

    # ------------------------------------------------------------------
    # extract
    # ------------------------------------------------------------------

    def extract(self) -> Generator[dict[str, Any], None, None]:
        """Yield one raw dict per study JSON file."""
        if self.file_path:
            path = Path(self.file_path)
            if path.is_file():
                record = self._read_json_file(path)
                if record is not None:
                    yield record
                return

        # Fall back to scanning data_dir for *.json
        data_dir = Path(self.data_dir) if self.data_dir else None
        if data_dir and data_dir.is_dir():
            for json_file in sorted(data_dir.glob("*.json")):
                record = self._read_json_file(json_file)
                if record is not None:
                    yield record

    def _read_json_file(self, path: Path) -> dict[str, Any] | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Skipping %s: %s", path, exc)
            return None

    # ------------------------------------------------------------------
    # transform
    # ------------------------------------------------------------------

    def transform(self, record: dict[str, Any]) -> dict[str, Any] | None:
        study_id = record.get("accession", "")
        if not study_id:
            logger.warning("Skipping PRIDE record — missing or empty accession")
            return None

        samples: list[dict[str, Any]] = []
        measurements: list[dict[str, Any]] = []

        for sample_raw in record.get("samples", []):
            sample_id = sample_raw.get("sample_id", "")
            if not sample_id:
                logger.warning("Skipping sample with missing sample_id in study %s", study_id)
                continue
            tissue_id = sample_raw.get("uberon_id") or None

            sample = {
                "sample_id": sample_id,
                "sample_type": "proteomics",
                "organism_part": sample_raw.get("organism_part"),
                "tissue_id": tissue_id,
                "source": self.source_name,
                "sources": [self.source_name],
                "organization_id": self.organization_id,
            }
            samples.append(sample)

            for protein_raw in sample_raw.get("proteins", []):
                uniprot_accession = protein_raw.get("uniprot_accession", "")
                if not uniprot_accession:
                    logger.warning(
                        "Skipping protein entry with missing uniprot_accession in study %s sample %s",
                        study_id,
                        sample_id,
                    )
                    continue
                protein_id = f"UNIPROT:{uniprot_accession}"
                measurement_id = f"{study_id}:{sample_id}:{protein_id}"

                raw_abundance = protein_raw.get("abundance")
                try:
                    value: float | None = float(raw_abundance) if raw_abundance is not None else None
                except (ValueError, TypeError):
                    logger.warning(
                        "Non-numeric abundance %r for %s in study %s — setting to None",
                        raw_abundance,
                        protein_id,
                        study_id,
                    )
                    value = None

                measurements.append({
                    "measurement_id": measurement_id,
                    "study_id": study_id,
                    "sample_id": sample_id,
                    "protein_id": protein_id,
                    "value": value,
                    "source": self.source_name,
                    "sources": [self.source_name],
                    "organization_id": self.organization_id,
                })

        return {
            "study_id": study_id,
            "title": record.get("title"),
            "study_type": "proteomics",
            "organization_id": self.organization_id,
            "source": self.source_name,
            "sources": [self.source_name],
            "_samples": samples,
            "_measurements": measurements,
        }

    # ------------------------------------------------------------------
    # load_batch
    # ------------------------------------------------------------------

    def load_batch(self, batch: list[dict[str, Any]]) -> dict[str, int]:
        nodes_created = 0
        relationships_created = 0

        # --- Study nodes ---
        studies = [
            {k: v for k, v in r.items() if not k.startswith("_")}
            for r in batch
        ]
        nc, rc = self._load_studies(studies)
        nodes_created += nc
        relationships_created += rc

        # --- Samples + Measurements ---
        for record in batch:
            study_id = record["study_id"]
            for sample in record.get("_samples", []):
                nc, rc = self._load_sample(study_id, sample)
                nodes_created += nc
                relationships_created += rc

            for measurement in record.get("_measurements", []):
                nc, rc = self._load_measurement(measurement)
                nodes_created += nc
                relationships_created += rc

        return {"nodes_created": nodes_created, "relationships_created": relationships_created}

    # ------------------------------------------------------------------
    # private helpers
    # ------------------------------------------------------------------

    def _load_studies(self, studies: list[dict[str, Any]]) -> tuple[int, int]:
        if not studies:
            return 0, 0
        query = """
            UNWIND $studies AS s
            MERGE (study:Study {study_id: s.study_id})
            ON CREATE SET
                study.title        = s.title,
                study.study_type   = s.study_type,
                study.source       = s.source,
                study.sources      = s.sources,
                study.organization_id = s.organization_id,
                study.created_at   = datetime()
            ON MATCH SET
                study.sources    = [x IN study.sources WHERE x <> s.source] + [s.source],
                study.updated_at = datetime()
            RETURN count(study) AS cnt
        """
        result = self.execute_cypher(query, {"studies": studies})
        cnt = result[0]["cnt"] if result else 0
        return cnt, 0

    def _load_sample(self, study_id: str, sample: dict[str, Any]) -> tuple[int, int]:
        nodes_created = 0
        relationships_created = 0

        # Merge sample node + HAS_SAMPLE edge
        # Use study_id + sample_id as the merge key to avoid cross-study collisions.
        query = """
            MATCH (study:Study {study_id: $study_id})
            MERGE (s:Sample {study_id: $study_id, sample_id: $sample_id})
            ON CREATE SET
                s.sample_type     = $sample_type,
                s.organism_part   = $organism_part,
                s.tissue_id       = $tissue_id,
                s.source          = $source,
                s.sources         = $sources,
                s.organization_id = $organization_id,
                s.created_at      = datetime()
            ON MATCH SET
                s.sources     = [x IN s.sources WHERE x <> $source] + [$source],
                s.updated_at  = datetime()
            MERGE (study)-[r:HAS_SAMPLE]->(s)
            ON CREATE SET r.created_at = datetime()
            RETURN count(s) AS cnt
        """
        result = self.execute_cypher(query, {
            "study_id": study_id,
            "sample_id": sample.get("sample_id"),
            "sample_type": sample.get("sample_type"),
            "organism_part": sample.get("organism_part"),
            "tissue_id": sample.get("tissue_id"),
            "source": sample.get("source"),
            "sources": sample.get("sources"),
            "organization_id": sample.get("organization_id"),
        })
        cnt = result[0]["cnt"] if result else 0
        nodes_created += cnt
        relationships_created += 1  # HAS_SAMPLE

        # SAMPLE_FROM_TISSUE edge — only when tissue_id is present
        tissue_id = sample.get("tissue_id")
        if tissue_id:
            tissue_query = """
                MATCH (s:Sample {study_id: $study_id, sample_id: $sample_id})
                MERGE (t:Tissue {tissue_id: $tissue_id})
                ON CREATE SET
                    t.name            = $organism_part,
                    t.source          = $source,
                    t.sources         = [$source],
                    t.organization_id = $organization_id,
                    t.created_at      = datetime()
                ON MATCH SET
                    t.sources    = [x IN t.sources WHERE x <> $source] + [$source],
                    t.updated_at = datetime()
                MERGE (s)-[r:SAMPLE_FROM_TISSUE]->(t)
                ON CREATE SET r.created_at = datetime()
                RETURN count(t) AS cnt
            """
            t_result = self.execute_cypher(tissue_query, {
                "study_id": study_id,
                "sample_id": sample.get("sample_id"),
                "tissue_id": tissue_id,
                "organism_part": sample.get("organism_part"),
                "source": sample.get("source"),
                "organization_id": sample.get("organization_id"),
            })
            t_cnt = t_result[0]["cnt"] if t_result else 0
            nodes_created += t_cnt
            relationships_created += 1  # SAMPLE_FROM_TISSUE

        return nodes_created, relationships_created

    def _load_measurement(self, measurement: dict[str, Any]) -> tuple[int, int]:
        """Create Measurement node + HAS_MEASUREMENT + QUANTIFIES edges.

        QUANTIFIES uses MATCH on Protein (not MERGE). If the Protein node does
        not exist, the Cypher runs but creates no QUANTIFIES edge; we log a
        WARNING so operators know which accessions are missing.
        """
        query = """
            MATCH (s:Sample {study_id: $study_id, sample_id: $sample_id})
            MERGE (m:Measurement {measurement_id: $measurement_id})
            ON CREATE SET
                m.study_id        = $study_id,
                m.sample_id       = $sample_id,
                m.protein_id      = $protein_id,
                m.value           = $value,
                m.source          = $source,
                m.sources         = $sources,
                m.organization_id = $organization_id,
                m.created_at      = datetime()
            ON MATCH SET
                m.sources    = [x IN m.sources WHERE x <> $source] + [$source],
                m.updated_at = datetime()
            MERGE (s)-[r:HAS_MEASUREMENT]->(m)
            ON CREATE SET r.created_at = datetime()
            WITH m
            OPTIONAL MATCH (p:Protein {protein_id: $protein_id})
            FOREACH (_ IN CASE WHEN p IS NOT NULL THEN [1] ELSE [] END |
                MERGE (m)-[:QUANTIFIES]->(p)
            )
            RETURN count(m) AS cnt, p IS NOT NULL AS protein_found
        """
        result = self.execute_cypher(query, {
            "measurement_id": measurement.get("measurement_id"),
            "study_id": measurement.get("study_id"),
            "sample_id": measurement.get("sample_id"),
            "protein_id": measurement.get("protein_id"),
            "value": measurement.get("value"),
            "source": measurement.get("source"),
            "sources": measurement.get("sources"),
            "organization_id": measurement.get("organization_id"),
        })
        cnt = result[0]["cnt"] if result else 0
        protein_found = result[0]["protein_found"] if result else False

        if not protein_found:
            logger.warning(
                "Protein not found for %s — QUANTIFIES edge skipped",
                measurement.get("protein_id"),
            )

        rels = 1  # HAS_MEASUREMENT always
        if protein_found:
            rels += 1  # QUANTIFIES
        return cnt, rels
