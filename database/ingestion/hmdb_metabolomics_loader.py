from __future__ import annotations

import logging
from typing import Any

from database.ingestion.base_loader import FileBasedLoader, normalize_disease_name
from database.ingestion.utils import resolve_compound_id

logger = logging.getLogger(__name__)


class HMDBMetabolomicsLoader(FileBasedLoader):
    """Load HMDB human metabolome data for the metabolomics discipline template.

    Filters to human endogenous compounds (at least one biofluid/tissue location
    OR is_endogenous=True). Writes Compound, FOUND_IN, BIOMARKER_FOR, and
    PARTICIPATES_IN. Does not write PROCESSES or LINKED_TO_DISEASE.
    """

    source_name = "hmdb_metabolomics"

    def transform(self, record: dict[str, Any]) -> dict[str, Any] | None:
        hmdb_id = record.get("hmdb_id")
        if not hmdb_id:
            return None

        biofluid_locations = record.get("biofluid_locations") or []
        tissue_locations = record.get("tissue_locations") or []
        is_endogenous = record.get("is_endogenous", False)

        if not biofluid_locations and not tissue_locations and not is_endogenous:
            return None

        try:
            compound_id = resolve_compound_id(
                record.get("inchi_key"),
                record.get("pubchem_cid"),
            )
        except ValueError:
            logger.warning("Skipping HMDB:%s — no inchi_key or pubchem_cid", hmdb_id)
            return None

        return {
            "compound_id": compound_id,
            "hmdb_id": hmdb_id,
            "name": record.get("name"),
            "inchi_key": record.get("inchi_key"),
            "pubchem_cid": record.get("pubchem_cid"),
            "chemical_formula": record.get("chemical_formula"),
            "molecular_weight": record.get("molecular_weight"),
            "smiles": record.get("smiles"),
            "inchi": record.get("inchi"),
            "cas_number": record.get("cas_number"),
            "chebi_id": record.get("chebi_id"),
            "kegg_id": record.get("kegg_id"),
            "organization_id": self.organization_id,
            "_locations": list(set(biofluid_locations + tissue_locations)),
            "_diseases": record.get("_diseases", []),
            "_pathways": record.get("_pathways", []),
        }

    def load_batch(self, batch: list[dict[str, Any]]) -> dict[str, int]:
        nodes_created = self._load_compounds(batch)
        rels_created = 0
        for record in batch:
            self._load_found_in(record)
            self._load_biomarker_for(record)
            rels_created += self._load_participates_in(record)
        return {"nodes_created": nodes_created, "relationships_created": rels_created}

    def _load_compounds(self, batch: list[dict[str, Any]]) -> int:
        records = [{k: v for k, v in r.items() if not k.startswith("_")} for r in batch]
        query = """
            UNWIND $compounds AS c
            MERGE (compound:Compound {compound_id: c.compound_id})
            ON CREATE SET
                compound.hmdb_id = c.hmdb_id,
                compound.name = c.name,
                compound.inchi_key = c.inchi_key,
                compound.pubchem_cid = c.pubchem_cid,
                compound.chemical_formula = c.chemical_formula,
                compound.molecular_weight = c.molecular_weight,
                compound.smiles = c.smiles,
                compound.inchi = c.inchi,
                compound.cas_number = c.cas_number,
                compound.chebi_id = c.chebi_id,
                compound.kegg_id = c.kegg_id,
                compound.organization_id = c.organization_id,
                compound.source = 'hmdb_metabolomics',
                compound.sources = ['hmdb_metabolomics'],
                compound.created_at = datetime()
            ON MATCH SET
                compound.updated_at = datetime()
            RETURN count(compound) AS count
        """
        result = self.execute_cypher(query, {"compounds": records})
        return result[0]["count"] if result else 0

    def _load_found_in(self, record: dict[str, Any]) -> None:
        for location in record.get("_locations", []):
            query = """
                MATCH (c:Compound {compound_id: $compound_id})
                MERGE (b:BodySite {bodysite_id: $bodysite_id})
                ON CREATE SET b.name = $name, b.created_at = datetime()
                MERGE (c)-[r:FOUND_IN]->(b)
                ON CREATE SET r.source = 'hmdb_metabolomics', r.created_at = datetime()
            """
            self.execute_cypher(query, {
                "compound_id": record["compound_id"],
                "bodysite_id": f"HMDB_SITE:{location.lower().replace(' ', '_')}",
                "name": location,
            })

    def _load_biomarker_for(self, record: dict[str, Any]) -> None:
        for disease in record.get("_diseases", []):
            name = disease.get("name")
            if not name:
                continue
            name_normalized = normalize_disease_name(name)
            query = """
                MATCH (c:Compound {compound_id: $compound_id})
                MERGE (d:Disease {name_normalized: $name_normalized})
                ON CREATE SET d.name = $name, d.created_at = datetime()
                MERGE (c)-[r:BIOMARKER_FOR]->(d)
                ON CREATE SET
                    r.evidence_level = $evidence,
                    r.source = 'hmdb_metabolomics',
                    r.created_at = datetime()
            """
            self.execute_cypher(query, {
                "compound_id": record["compound_id"],
                "name_normalized": name_normalized,
                "name": name,
                "evidence": disease.get("evidence", ""),
            })

    def _load_participates_in(self, record: dict[str, Any]) -> int:
        rels_created = 0
        for pathway in record.get("_pathways", []):
            kegg_id = pathway.get("kegg_id")
            smpdb_id = pathway.get("smpdb_id")
            name = pathway.get("name")

            if kegg_id:
                pathway_id = f"KEGG:{kegg_id}"
            elif smpdb_id:
                pathway_id = f"SMPDB:{smpdb_id}"
            elif name:
                pathway_id = f"HMDB_PATH:{name.lower().replace(' ', '_')}"
            else:
                continue

            query = """
                MATCH (c:Compound {compound_id: $compound_id})
                MERGE (p:Pathway {pathway_id: $pathway_id})
                ON CREATE SET p.name = $pathway_name, p.created_at = datetime()
                MERGE (c)-[r:PARTICIPATES_IN]->(p)
                ON CREATE SET r.source = 'hmdb_metabolomics', r.created_at = datetime()
                RETURN count(r) AS count
            """
            result = self.execute_cypher(query, {
                "compound_id": record["compound_id"],
                "pathway_id": pathway_id,
                "pathway_name": name or pathway_id,
            })
            rels_created += result[0]["count"] if result else 0
        return rels_created

    def extract(self):
        from database.ingestion.hmdb_loader import HMDBLoader
        delegate = HMDBLoader(
            file_path=self.file_path,
            driver=self.driver,
            organization_id=self.organization_id,
        )
        yield from delegate.extract()
