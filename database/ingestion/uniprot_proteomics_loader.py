"""UniProt Swiss-Prot proteomics loader.

Parses UniProt Swiss-Prot flat (.dat) files and writes:
- Protein nodes
- Modification nodes + MODIFIES edges (from PTM FT lines)
- PARTICIPATES_IN edges to Pathway stubs (from Reactome DR lines)
- ASSOCIATED_WITH_DISEASE edges to Disease stubs (from MIM phenotype DR lines)

Only Homo sapiens records are loaded; all other organisms are filtered out.
"""

from __future__ import annotations

import logging
from typing import Any, Generator

from database.ingestion.base_loader import FileBasedLoader

logger = logging.getLogger(__name__)

# Maps note prefix → (ptm_type, residue_letter)
_PTM_TYPE_MAP: list[tuple[str, str, str]] = [
    ("Phosphoserine", "phosphorylation", "S"),
    ("Phosphothreonine", "phosphorylation", "T"),
    ("Phosphotyrosine", "phosphorylation", "Y"),
    ("Acetyllysine", "acetylation", "K"),
    ("Methylarginine", "methylation", "R"),
    ("Ubiquitinyllysine", "ubiquitination", "K"),
    # Generic prefix checks (order matters — more specific first above)
    ("Phospho", "phosphorylation", ""),
    ("Acetyl", "acetylation", ""),
    ("Methyl", "methylation", ""),
    ("Ubiquitin", "ubiquitination", ""),
    ("Glyco", "glycosylation", ""),
    ("Sumoyl", "sumoylation", ""),
]


def _parse_dat_records(file_path: str) -> Generator[dict[str, Any], None, None]:
    """Parse a UniProt Swiss-Prot flat (.dat) file, yielding one dict per record."""
    current: dict[str, Any] = _empty_record()
    pending_ft_position: str | None = None
    pending_ft_type: str | None = None

    with open(file_path, "r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n")
            if len(line) < 2:
                continue

            tag = line[:2]
            # Guard against lines shorter than 5 chars (no content after tag)
            content = line[5:] if len(line) > 5 else ""

            if tag == "//":
                # End of record — flush and reset
                yield current
                current = _empty_record()
                pending_ft_position = None
                pending_ft_type = None
                continue

            if tag == "AC":
                # First AC line wins; may have multiple accessions separated by "; "
                if current["accession"] is None:
                    raw_acs = content.strip().rstrip(";")
                    parts = [p.strip() for p in raw_acs.split(";") if p.strip()]
                    if parts:
                        current["accession"] = parts[0]

            elif tag == "DE":
                # RecName: Full=... or SubName: Full=...
                if current["name"] is None and "Full=" in content:
                    name_part = content.split("Full=", 1)[1].rstrip(";").strip()
                    current["name"] = name_part

            elif tag == "GN":
                # GN   Name=TP53; or GN   Name=TP53; Synonyms=...
                if current["gene_name"] is None and "Name=" in content:
                    gn_part = content.split("Name=", 1)[1]
                    gn_part = gn_part.split(";")[0].strip()
                    current["gene_name"] = gn_part

            elif tag == "OS":
                # Organism species line — accumulate (may span multiple lines)
                current["organism"] = (current["organism"] or "") + " " + content.strip()
                current["organism"] = current["organism"].strip()

            elif tag == "DR":
                # Cross-reference line
                parts = [p.strip() for p in content.split(";")]
                if parts and parts[0] == "Reactome" and len(parts) >= 2:
                    reactome_id = parts[1].strip()
                    if reactome_id:
                        current["reactome_ids"].append(reactome_id)
                elif parts and parts[0] == "MIM" and len(parts) >= 3:
                    mim_id = parts[1].strip()
                    mim_type = parts[2].strip().rstrip(".")
                    if mim_type == "phenotype" and mim_id:
                        current["mim_phenotypes"].append(mim_id)

            elif tag == "FT":
                # Feature table line
                # Two sub-formats:
                # FT   MOD_RES         15           ← feature key + position
                # FT                   /note="..."  ← continuation (note or evidence)
                stripped = content  # content starts at column 5
                if stripped.strip().startswith("/note="):
                    # Continuation: belongs to the most recent FT MOD_RES
                    # NOTE: real UniProt continuation lines have 16+ leading spaces before
                    # /note=, so we must strip() before the prefix check.
                    if pending_ft_type == "MOD_RES" and pending_ft_position is not None:
                        note_raw = stripped.strip()[len("/note="):].strip().strip('"')
                        current["ptm_features"].append({
                            "position": pending_ft_position,
                            "note": note_raw,
                        })
                        # Reset — don't carry forward to evidence line
                        pending_ft_type = None
                        pending_ft_position = None
                    # else: note on a non-MOD_RES feature, ignore
                elif stripped.startswith("/"):
                    # Other continuation (/evidence etc.) — just clear if we haven't
                    # recorded a note yet; avoids stale state
                    pass
                else:
                    # New feature key line: "MOD_RES         15"
                    tokens = stripped.split()
                    if tokens:
                        ft_key = tokens[0]
                        position = tokens[1] if len(tokens) >= 2 else None
                        pending_ft_type = ft_key
                        pending_ft_position = position if ft_key == "MOD_RES" else None


def _empty_record() -> dict[str, Any]:
    return {
        "accession": None,
        "name": None,
        "gene_name": None,
        "organism": None,
        "reactome_ids": [],
        "mim_phenotypes": [],
        "ptm_features": [],
    }


def _determine_ptm(note: str) -> tuple[str | None, str | None]:
    """Return (ptm_type, residue_letter) from a MOD_RES note, or (None, None)."""
    for prefix, ptm_type, residue in _PTM_TYPE_MAP:
        if note.startswith(prefix):
            return ptm_type, residue if residue else None
    return None, None


class UniProtProteomicsLoader(FileBasedLoader):
    """Load UniProt Swiss-Prot proteomics data.

    Reads a Swiss-Prot .dat flat file, filters to Homo sapiens records, and
    writes Protein nodes, Modification nodes + MODIFIES edges, Pathway stubs +
    PARTICIPATES_IN edges (Reactome), and Disease stubs + ASSOCIATED_WITH_DISEASE
    edges (MIM phenotype cross-references).
    """

    source_name = "uniprot"

    def extract(self) -> Generator[dict[str, Any], None, None]:
        """Parse the .dat file and yield Homo sapiens records only."""
        for record in _parse_dat_records(self.file_path):
            organism = record.get("organism") or ""
            if "Homo sapiens" not in organism:
                continue
            # Set defaults for missing fields
            record.setdefault("accession", None)
            record.setdefault("name", None)
            record.setdefault("gene_name", "")
            record.setdefault("reactome_ids", [])
            record.setdefault("mim_phenotypes", [])
            record.setdefault("ptm_features", [])
            yield record

    def transform(self, record: dict[str, Any]) -> dict[str, Any] | None:
        accession = record.get("accession")
        if not accession:
            logger.warning("Skipping UniProt record — no accession: %r", record.get("name"))
            return None

        protein_id = f"UNIPROT:{accession}"

        # Parse PTM features
        modifications: list[dict[str, Any]] = []
        for feat in record.get("ptm_features", []):
            position = feat.get("position")
            note = feat.get("note") or ""
            if position is None:
                logger.warning(
                    "Skipping MOD_RES for %s — position is None (note: %r)", accession, note
                )
                continue

            ptm_type, residue = _determine_ptm(note)
            if ptm_type is None:
                # Unknown PTM type — skip
                continue
            if residue is None:
                logger.warning(
                    "Skipping MOD_RES for %s pos %s — cannot determine residue from note: %r",
                    accession,
                    position,
                    note,
                )
                continue

            site = f"{residue}{position}"
            modification_id = f"{accession}:{ptm_type}:{site}"
            modifications.append({
                "modification_id": modification_id,
                "ptm_type": ptm_type,
                "site": site,
                "position": int(position) if position.isdigit() else position,
                "note": note,
                "source": "uniprot",
                "sources": ["uniprot"],
                "organization_id": self.organization_id,
            })

        return {
            "protein_id": protein_id,
            "uniprot_accession": accession,
            "name": record.get("name") or "",
            "gene_name": record.get("gene_name") or "",
            "organism": record.get("organism") or "",
            "source": "uniprot",
            "sources": ["uniprot"],
            "organization_id": self.organization_id,
            "_modifications": modifications,
            "_reactome_ids": record.get("reactome_ids", []),
            "_mim_phenotypes": record.get("mim_phenotypes", []),
        }

    def load_batch(self, batch: list[dict[str, Any]]) -> dict[str, int]:
        nodes_created = 0
        rels_created = 0

        with self.driver.session(database=self.database) as session:
            # 1. Write Protein nodes
            protein_records = [
                {k: v for k, v in r.items() if not k.startswith("_")}
                for r in batch
            ]
            result = session.run(
                """
                UNWIND $proteins AS p
                MERGE (prot:Protein {protein_id: p.protein_id})
                ON CREATE SET
                    prot.uniprot_accession = p.uniprot_accession,
                    prot.name = p.name,
                    prot.gene_name = p.gene_name,
                    prot.organism = p.organism,
                    prot.organization_id = p.organization_id,
                    prot.source = p.source,
                    prot.sources = p.sources,
                    prot.created_at = datetime()
                ON MATCH SET
                    prot.updated_at = datetime()
                RETURN count(prot) AS count
                """,
                {"proteins": protein_records},
            )
            row = result.single()
            if row:
                nodes_created += row["count"]

            # 2. Write Modification nodes + MODIFIES edges
            for record in batch:
                for mod in record.get("_modifications", []):
                    result = session.run(
                        """
                        MATCH (prot:Protein {protein_id: $protein_id})
                        MERGE (m:Modification {modification_id: $modification_id})
                        ON CREATE SET
                            m.ptm_type = $ptm_type,
                            m.site = $site,
                            m.position = $position,
                            m.note = $note,
                            m.organization_id = $organization_id,
                            m.source = $source,
                            m.sources = $sources,
                            m.created_at = datetime()
                        ON MATCH SET m.updated_at = datetime()
                        MERGE (m)-[r:MODIFIES]->(prot)
                        ON CREATE SET r.source = $source, r.created_at = datetime()
                        RETURN count(m) AS count
                        """,
                        {
                            "protein_id": record["protein_id"],
                            "modification_id": mod["modification_id"],
                            "ptm_type": mod["ptm_type"],
                            "site": mod["site"],
                            "position": mod["position"],
                            "note": mod["note"],
                            "organization_id": mod["organization_id"],
                            "source": mod["source"],
                            "sources": mod["sources"],
                        },
                    )
                    row = result.single()
                    if row:
                        nodes_created += row["count"]
                        rels_created += row["count"]

            # 3. Write PARTICIPATES_IN edges to Pathway stubs (Reactome)
            for record in batch:
                for reactome_id in record.get("_reactome_ids", []):
                    pathway_id = f"REACTOME:{reactome_id}"
                    result = session.run(
                        """
                        MATCH (prot:Protein {protein_id: $protein_id})
                        MERGE (pw:Pathway {pathway_id: $pathway_id})
                        ON CREATE SET
                            pw.reactome_id = $reactome_id,
                            pw.organization_id = $organization_id,
                            pw.source = 'uniprot',
                            pw.sources = ['uniprot'],
                            pw.created_at = datetime()
                        MERGE (prot)-[r:PARTICIPATES_IN]->(pw)
                        ON CREATE SET r.source = 'uniprot', r.created_at = datetime()
                        RETURN count(r) AS count
                        """,
                        {
                            "protein_id": record["protein_id"],
                            "pathway_id": pathway_id,
                            "reactome_id": reactome_id,
                            "organization_id": record["organization_id"],
                        },
                    )
                    row = result.single()
                    if row:
                        rels_created += row["count"]

            # 4. Write ASSOCIATED_WITH_DISEASE edges to Disease stubs (MIM phenotype)
            for record in batch:
                for mim_id in record.get("_mim_phenotypes", []):
                    disease_id = f"OMIM:{mim_id}"
                    result = session.run(
                        """
                        MATCH (prot:Protein {protein_id: $protein_id})
                        MERGE (d:Disease {disease_id: $disease_id})
                        ON CREATE SET
                            d.omim_id = $mim_id,
                            d.organization_id = $organization_id,
                            d.source = 'uniprot',
                            d.sources = ['uniprot'],
                            d.created_at = datetime()
                        MERGE (prot)-[r:ASSOCIATED_WITH_DISEASE]->(d)
                        ON CREATE SET r.source = 'uniprot', r.created_at = datetime()
                        RETURN count(r) AS count
                        """,
                        {
                            "protein_id": record["protein_id"],
                            "disease_id": disease_id,
                            "mim_id": mim_id,
                            "organization_id": record["organization_id"],
                        },
                    )
                    row = result.single()
                    if row:
                        rels_created += row["count"]

        return {"nodes_created": nodes_created, "relationships_created": rels_created}
