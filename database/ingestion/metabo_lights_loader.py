from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Generator

from database.ingestion.base_loader import FileBasedLoader

logger = logging.getLogger(__name__)


class MetaboLightsLoader(FileBasedLoader):
    """Load MetaboLights metabolomics study data.

    Expects file_path to be a JSON file or directory of JSON files.
    Each JSON file represents one study with samples and measurements.

    Writes: Study, Sample, Measurement, HAS_SAMPLE, HAS_MEASUREMENT,
    QUANTIFIES, COLLECTED_FROM.

    Note: Compound nodes must be pre-loaded (e.g. by running --metabolomics-hmdb
    or --metacyc first) for QUANTIFIES edges to be created. Measurements whose
    compound_id does not match an existing Compound node are silently skipped by
    the MATCH clause in _load_measurement.
    """

    source_name = "metabo_lights"

    def extract(self) -> Generator[dict[str, Any], None, None]:
        path = Path(self.file_path)
        if path.is_file():
            yield from self._read_study_file(path)
        elif path.is_dir():
            for json_file in sorted(path.glob("*.json")):
                yield from self._read_study_file(json_file)

    def _read_study_file(self, path: Path) -> Generator[dict[str, Any], None, None]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                yield from data
            else:
                yield data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Skipping %s: %s", path, exc)

    def transform(self, record: dict[str, Any]) -> dict[str, Any] | None:
        study_id = record.get("study_id")
        if not study_id:
            return None

        samples = []
        for sample in record.get("_samples", []):
            sample_id = sample.get("sample_id", "")
            measurements = []
            for m in sample.get("measurements", []):
                compound_id = m.get("compound_id", "")
                measurement_id = f"{study_id}:{sample_id}:{compound_id}"
                try:
                    value = float(m["value"]) if m.get("value") is not None else None
                except (ValueError, TypeError):
                    logger.warning(
                        "Non-numeric measurement value %r — setting to None",
                        m.get("value"),
                    )
                    value = None
                measurements.append({
                    **m,
                    "measurement_id": measurement_id,
                    "value": value,
                })
            samples.append({**sample, "measurements": measurements})

        return {
            "study_id": study_id,
            "title": record.get("title"),
            "description": record.get("description"),
            "organism": record.get("organism"),
            "study_type": "metabolomics",
            "organization_id": self.organization_id,
            "_samples": samples,
        }

    def load_batch(self, batch: list[dict[str, Any]]) -> dict[str, int]:
        studies = [{k: v for k, v in r.items() if not k.startswith("_")} for r in batch]
        self._load_studies(studies)
        for record in batch:
            for sample in record.get("_samples", []):
                self._load_sample(record["study_id"], sample)
        return {"nodes_created": 0, "relationships_created": 0}

    def _load_studies(self, studies: list[dict[str, Any]]) -> None:
        query = """
            UNWIND $studies AS s
            MERGE (study:Study {study_id: s.study_id})
            ON CREATE SET
                study.title = s.title,
                study.description = s.description,
                study.organism = s.organism,
                study.study_type = s.study_type,
                study.organization_id = s.organization_id,
                study.source = 'metabo_lights',
                study.sources = ['metabo_lights'],
                study.created_at = datetime()
            ON MATCH SET
                study.updated_at = datetime()
        """
        self.execute_cypher(query, {"studies": studies})

    def _load_sample(self, study_id: str, sample: dict[str, Any]) -> None:
        sample_data = {k: v for k, v in sample.items() if k != "measurements"}
        sample_data["source"] = "metabo_lights"
        sample_data["sample_type"] = "metabolomics"
        sample_data["organization_id"] = self.organization_id

        query = """
            MATCH (study:Study {study_id: $study_id})
            MERGE (s:Sample {sample_id: $sample_id})
            ON CREATE SET
                s.sample_name = $sample_name,
                s.sample_type = $sample_type,
                s.organization_id = $organization_id,
                s.source = $source,
                s.created_at = datetime()
            ON MATCH SET s.updated_at = datetime()
            MERGE (study)-[:HAS_SAMPLE]->(s)
        """
        self.execute_cypher(query, {
            "study_id": study_id,
            "sample_id": sample.get("sample_id"),
            "sample_name": sample.get("sample_name"),
            "sample_type": "metabolomics",
            "organization_id": self.organization_id,
            "source": "metabo_lights",
        })

        body_site = sample.get("body_site")
        if body_site:
            self._load_collected_from(sample["sample_id"], body_site)

        for m in sample.get("measurements", []):
            self._load_measurement(sample["sample_id"], m)

    def _load_collected_from(self, sample_id: str, body_site: str) -> None:
        query = """
            MATCH (s:Sample {sample_id: $sample_id})
            MERGE (b:BodySite {bodysite_id: $bodysite_id})
            ON CREATE SET b.name = $name, b.created_at = datetime()
            MERGE (s)-[:COLLECTED_FROM]->(b)
        """
        self.execute_cypher(query, {
            "sample_id": sample_id,
            "bodysite_id": f"SITE:{body_site.lower().replace(' ', '_')}",
            "name": body_site,
        })

    def _load_measurement(self, sample_id: str, measurement: dict[str, Any]) -> None:
        compound_id = measurement.get("compound_id")
        if not compound_id:
            logger.warning("Skipping measurement — no compound_id")
            return
        query = """
            MATCH (s:Sample {sample_id: $sample_id})
            MATCH (c:Compound {compound_id: $compound_id})
            MERGE (m:Measurement {measurement_id: $measurement_id})
            ON CREATE SET
                m.value = $value,
                m.unit = $unit,
                m.assay_type = $assay_type,
                m.source = 'metabo_lights',
                m.sources = ['metabo_lights'],
                m.created_at = datetime()
            ON MATCH SET m.updated_at = datetime()
            MERGE (s)-[:HAS_MEASUREMENT]->(m)
            MERGE (m)-[:QUANTIFIES]->(c)
        """
        self.execute_cypher(query, {
            "sample_id": sample_id,
            "compound_id": measurement.get("compound_id"),
            "measurement_id": measurement.get("measurement_id"),
            "value": measurement.get("value"),
            "unit": measurement.get("unit"),
            "assay_type": measurement.get("assay_type"),
        })
