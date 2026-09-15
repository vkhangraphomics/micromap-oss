"""PhosphoSitePlus proteomics loader.

Parses PhosphoSitePlus tab-separated flat files (one file per PTM type) and
writes Modification nodes with MODIFIES edges to existing Protein nodes.

Supported file naming convention: *_site_dataset* (optionally .gz compressed).
Each file contains comment lines starting with '#' at the top, followed by a
TSV header line and data rows.

Only human records (ORGANISM == "human") are loaded.
"""

from __future__ import annotations

import csv
import gzip
import io
import logging
import os
import re
from typing import Any, Generator, Iterator

from database.ingestion.base_loader import FileBasedLoader

logger = logging.getLogger(__name__)

# Map MOD_RSD suffix → ptm_type
_SUFFIX_TO_PTM: dict[str, str] = {
    "-p": "phosphorylation",
    "-ac": "acetylation",
    "-ub": "ubiquitination",
    "-m1": "methylation",
    "-m2": "methylation",
    "-m3": "methylation",
    "-ga": "glycosylation",
    "-gl": "glycosylation",
    "-sc": "glycosylation",
    "-sm": "sumoylation",
    "-ne": "neddylation",
    "-pa": "palmitoylation",
    "-cr": "crotonylation",
}

# Regex to split MOD_RSD into site + suffix, e.g. "S15-p" → ("S15", "-p")
_MOD_RSD_RE = re.compile(r'^(.+?)(-(?:p|ac|ub|m[123]|ga|gl|sc|sm|ne|pa|cr))$')


def _ptm_from_suffix(mod_rsd: str) -> tuple[str | None, str | None]:
    """Return (site, ptm_type) from a MOD_RSD value, or (None, None) if unparseable."""
    m = _MOD_RSD_RE.match(mod_rsd.strip())
    if not m:
        return None, None
    site = m.group(1)
    suffix = m.group(2)
    ptm_type = _SUFFIX_TO_PTM.get(suffix)
    return site, ptm_type


def _open_tsv(path: str):
    """Open a possibly gzip-compressed TSV file and return a text-mode file object."""
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, "r", encoding="utf-8")


def _parse_phosphosite_file(path: str) -> Generator[dict[str, Any], None, None]:
    """Parse a PhosphoSitePlus TSV file, yielding one dict per data row.

    Comment lines (starting with '#') are stripped before CSV parsing.
    """
    with _open_tsv(path) as fh:
        lines = [line for line in fh if not line.startswith("#")]

    if not lines:
        logger.warning("PhosphoSite file is empty or all-comments: %s", path)
        return

    reader = csv.DictReader(io.StringIO("".join(lines)), delimiter="\t")
    for row in reader:
        yield dict(row)


class PhosphoSiteLoader(FileBasedLoader):
    """Load PhosphoSitePlus PTM data.

    Reads one or more PhosphoSitePlus flat files, filters to human records, and
    writes Modification nodes + MODIFIES edges pointing at existing Protein nodes.

    Constructor accepts either:
    - ``file_path``: path to a single dataset file (uses FileBasedLoader convention)
    - ``data_dir``: directory containing *_site_dataset* files (pass file_path=None
      and data_dir=... to the constructor)

    In practice the FileBasedLoader constructor requires a file_path argument.
    Pass ``file_path=None`` when you want directory-based scanning; the class
    stores the directory in ``self.data_dir``.
    """

    source_name = "phosphosite"

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
        self.data_dir = data_dir

    # ------------------------------------------------------------------
    # ETL pipeline
    # ------------------------------------------------------------------

    def extract(self) -> Iterator[dict[str, Any]]:
        """Yield human rows from one file (self.file_path) or all dataset files
        found under self.data_dir."""
        if self.file_path:
            yield from self._extract_file(self.file_path)
        elif self.data_dir:
            yield from self._extract_dir(self.data_dir)
        else:
            raise ValueError(
                "PhosphoSiteLoader requires either file_path or data_dir to be set"
            )

    def _extract_file(self, path: str) -> Iterator[dict[str, Any]]:
        for row in _parse_phosphosite_file(path):
            organism = (row.get("ORGANISM") or "").strip().lower()
            if organism != "human":
                continue
            yield row

    def _extract_dir(self, directory: str) -> Iterator[dict[str, Any]]:
        pattern = re.compile(r".*_site_dataset.*", re.IGNORECASE)
        found = False
        for fname in sorted(os.listdir(directory)):
            if pattern.match(fname):
                found = True
                fpath = os.path.join(directory, fname)
                logger.info("PhosphoSiteLoader: scanning %s", fpath)
                yield from self._extract_file(fpath)
        if not found:
            logger.warning(
                "PhosphoSiteLoader: no *_site_dataset* files found in %s", directory
            )

    def transform(self, record: dict[str, Any]) -> dict[str, Any] | None:
        acc_id = (record.get("ACC_ID") or "").strip()
        if not acc_id:
            logger.warning(
                "PhosphoSite: skipping row — empty ACC_ID (GENE=%r)", record.get("GENE")
            )
            return None

        mod_rsd = (record.get("MOD_RSD") or "").strip()
        site, ptm_type = _ptm_from_suffix(mod_rsd)

        if site is None or not site:
            logger.warning(
                "PhosphoSite: skipping row — MOD_RSD %r has no parseable site "
                "(ACC_ID=%r)",
                mod_rsd,
                acc_id,
            )
            return None

        if ptm_type is None:
            logger.warning(
                "PhosphoSite: skipping row — unknown PTM suffix in MOD_RSD %r "
                "(ACC_ID=%r)",
                mod_rsd,
                acc_id,
            )
            return None

        modification_id = f"{acc_id}:{ptm_type}:{site}"

        enzyme = (record.get("KINASE") or "").strip() or None

        lt_lit_raw = (record.get("LT_LIT") or "").strip()
        try:
            evidence_count = int(lt_lit_raw) if lt_lit_raw else 0
        except ValueError:
            evidence_count = 0

        return {
            "modification_id": modification_id,
            "uniprot_accession": acc_id,
            "ptm_type": ptm_type,
            "site": site,
            "gene": (record.get("GENE") or "").strip(),
            "protein_name": (record.get("PROTEIN") or "").strip(),
            "enzyme": enzyme,
            "evidence_count": evidence_count,
            "source": self.source_name,
            "sources": [self.source_name],
            "organization_id": self.organization_id,
        }

    def load_batch(self, batch: list[dict[str, Any]]) -> dict[str, int]:
        nodes_created = 0
        rels_created = 0

        with self.driver.session(database=self.database) as session:
            for mod in batch:
                protein_id = f"UNIPROT:{mod['uniprot_accession']}"
                result = session.run(
                    """
                    MATCH (prot:Protein {protein_id: $protein_id})
                    MERGE (m:Modification {modification_id: $modification_id})
                    ON CREATE SET
                        m.ptm_type            = $ptm_type,
                        m.site                = $site,
                        m.gene                = $gene,
                        m.protein_name        = $protein_name,
                        m.enzyme              = $enzyme,
                        m.evidence_count      = $evidence_count,
                        m.organization_id     = $organization_id,
                        m.source              = $source,
                        m.sources             = $sources,
                        m.created_at          = datetime()
                    ON MATCH SET
                        m.enzyme              = CASE WHEN $enzyme IS NOT NULL
                                                     THEN $enzyme ELSE m.enzyme END,
                        m.evidence_count      = CASE WHEN $evidence_count > m.evidence_count
                                                     THEN $evidence_count ELSE m.evidence_count END,
                        m.sources             = [x IN m.sources WHERE x <> $source] + [$source],
                        m.updated_at          = datetime()
                    MERGE (m)-[r:MODIFIES]->(prot)
                    ON CREATE SET r.source = $source, r.created_at = datetime()
                    RETURN count(m) AS count
                    """,
                    {
                        "protein_id": protein_id,
                        "modification_id": mod["modification_id"],
                        "ptm_type": mod["ptm_type"],
                        "site": mod["site"],
                        "gene": mod["gene"],
                        "protein_name": mod["protein_name"],
                        "enzyme": mod["enzyme"],
                        "evidence_count": mod["evidence_count"],
                        "organization_id": mod["organization_id"],
                        "source": mod["source"],
                        "sources": mod["sources"],
                    },
                )
                row = result.single()
                if row and row["count"] == 0:
                    logger.warning(
                        "PhosphoSite: no Protein found for %r — Modification dropped",
                        protein_id,
                    )
                elif row:
                    nodes_created += row["count"]
                    rels_created += row["count"]

        return {"nodes_created": nodes_created, "relationships_created": rels_created}
