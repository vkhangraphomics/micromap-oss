from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Generator

from database.ingestion.base_loader import FileBasedLoader
from database.ingestion.utils import resolve_compound_id

logger = logging.getLogger(__name__)


def _parse_dat_records(text: str) -> Generator[dict[str, list[str]], None, None]:
    """Parse MetaCyc flat-file format into dicts of field -> [values]."""
    current: dict[str, list[str]] = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#") or not line:
            continue
        if line == "//":
            if current:
                yield current
            current = {}
            continue
        if " - " in line:
            field, _, value = line.partition(" - ")
            current.setdefault(field.strip(), []).append(value.strip())
    if current:
        yield current


class MetaCycLoader(FileBasedLoader):
    """Load MetaCyc compounds and pathways from compounds.dat + pathways.dat.

    Expects file_path to be a directory containing compounds.dat and pathways.dat.
    Writes: Compound, Pathway, PARTICIPATES_IN.
    """

    source_name = "metacyc"

    def extract(self) -> Generator[dict[str, Any], None, None]:
        base = Path(self.file_path)
        compounds_dat = base / "compounds.dat"
        pathways_dat = base / "pathways.dat"

        if compounds_dat.exists():
            text = compounds_dat.read_text(encoding="latin-1")
            for rec in _parse_dat_records(text):
                rec["_type"] = "compound"
                yield rec

        if pathways_dat.exists():
            text = pathways_dat.read_text(encoding="latin-1")
            for rec in _parse_dat_records(text):
                rec["_type"] = "pathway"
                yield rec

    def transform(self, record: dict[str, Any]) -> dict[str, Any] | None:
        record_type = record.get("_type")

        if record_type == "compound":
            unique_id = (record.get("UNIQUE-ID") or [""])[0]
            name = (record.get("COMMON-NAME") or [""])[0]
            inchi_key_raw = (record.get("INCHI-KEY") or [""])[0]
            inchi_key = inchi_key_raw.replace("InChIKey=", "") or None
            pubchem_cid = (record.get("PUBCHEM-ID") or [""])[0] or None

            try:
                compound_id = resolve_compound_id(inchi_key, pubchem_cid)
            except ValueError:
                logger.warning("Skipping MetaCyc %s — no inchi_key or pubchem_cid", unique_id)
                return None

            in_pathways = record.get("IN-PATHWAY") or []
            return {
                "compound_id": compound_id,
                "metacyc_id": f"METACYC:{unique_id}",
                "name": name,
                "inchi_key": inchi_key,
                "pubchem_cid": pubchem_cid,
                "organization_id": self.organization_id,
                "_record_type": "compound",
                "_in_pathways": [f"METACYC:{p}" for p in in_pathways],
            }

        if record_type == "pathway":
            unique_id = (record.get("UNIQUE-ID") or [""])[0]
            name = (record.get("COMMON-NAME") or [""])[0]
            return {
                "pathway_id": f"METACYC:{unique_id}",
                "name": name,
                "organization_id": self.organization_id,
                "_record_type": "pathway",
            }

        return None

    def load_batch(self, batch: list[dict[str, Any]]) -> dict[str, int]:
        nodes_created = 0
        rels_created = 0
        compounds = [r for r in batch if r.get("_record_type") == "compound"]
        pathways = [r for r in batch if r.get("_record_type") == "pathway"]
        if compounds:
            nodes_created += self._load_compounds(compounds)
            rels_created += self._load_compound_pathways(compounds)
        if pathways:
            nodes_created += self._load_pathways(pathways)
        return {"nodes_created": nodes_created, "relationships_created": rels_created}

    def _load_compound_pathways(self, batch: list[dict[str, Any]]) -> int:
        pairs = [
            {"compound_id": r["compound_id"], "pathway_id": pid}
            for r in batch
            for pid in r.get("_in_pathways", [])
        ]
        if not pairs:
            return 0
        query = """
            UNWIND $pairs AS pair
            MATCH (c:Compound {compound_id: pair.compound_id})
            MERGE (p:Pathway {pathway_id: pair.pathway_id})
            ON CREATE SET p.source = 'metacyc', p.sources = ['metacyc'], p.created_at = datetime()
            MERGE (c)-[r:PARTICIPATES_IN]->(p)
            ON CREATE SET r.source = 'metacyc', r.created_at = datetime()
            RETURN count(r) AS count
        """
        result = self.execute_cypher(query, {"pairs": pairs})
        return result[0]["count"] if result else 0

    def _load_compounds(self, batch: list[dict[str, Any]]) -> int:
        records = [{k: v for k, v in r.items() if not k.startswith("_")} for r in batch]
        query = """
            UNWIND $compounds AS c
            MERGE (compound:Compound {compound_id: c.compound_id})
            ON CREATE SET
                compound.metacyc_id = c.metacyc_id,
                compound.name = c.name,
                compound.inchi_key = c.inchi_key,
                compound.pubchem_cid = c.pubchem_cid,
                compound.organization_id = c.organization_id,
                compound.source = 'metacyc',
                compound.sources = ['metacyc'],
                compound.created_at = datetime()
            ON MATCH SET
                compound.metacyc_id = COALESCE(compound.metacyc_id, c.metacyc_id),
                compound.updated_at = datetime()
            RETURN count(compound) AS count
        """
        result = self.execute_cypher(query, {"compounds": records})
        return result[0]["count"] if result else 0

    def _load_pathways(self, batch: list[dict[str, Any]]) -> int:
        records = [{k: v for k, v in r.items() if not k.startswith("_")} for r in batch]
        query = """
            UNWIND $pathways AS p
            MERGE (pathway:Pathway {pathway_id: p.pathway_id})
            ON CREATE SET
                pathway.name = p.name,
                pathway.organization_id = p.organization_id,
                pathway.source = 'metacyc',
                pathway.sources = ['metacyc'],
                pathway.created_at = datetime()
            ON MATCH SET
                pathway.name = COALESCE(pathway.name, p.name),
                pathway.updated_at = datetime()
            RETURN count(pathway) AS count
        """
        result = self.execute_cypher(query, {"pathways": records})
        return result[0]["count"] if result else 0
