"""Open Targets BioCypher adapter (#375).

Reads Open Targets Platform parquet directly via pyarrow — NOT a wrapper
around the upstream `biocypher/open-targets` package, which pins
`requires-python >=3.10,<3.11` and so cannot be installed alongside this
project (`>=3.11`). Re-implementing the handful of field mappings we
actually need is less work than bridging two Python versions.

Scope is intentionally narrow, matching #375's target-disease-drug
recommendation rather than the upstream package's full 40+ node / 50+ edge
reference graph:

- target  -> Gene nodes (Ensembl gene id)
- disease -> Disease nodes (EFO/MONDO/... ontology id)
- drug_molecule -> Drug nodes (ChEMBL id)
- association_overall_direct -> (Gene)-[:ASSOCIATED_WITH_DISEASE]->(Disease),
  the small pre-aggregated target-disease score table — not the much larger
  raw `evidence/` dataset (per-evidence-record across ~20 sources).

`path` is expected to mirror Open Targets' real release 25.03+ FTP layout
(output/<name>/*.parquet — snake_case & singular directory names since
25.03, a breaking rename from the pre-25.03 `output/etl/parquet/<name>/`
layout this adapter originally targeted, see #388): a directory containing
`target/`, `disease/`, `drug_molecule/`, and `association_overall_direct/`
subdirectories, each holding one or more `.parquet` part-files.

Field names verified against real files downloaded from
https://ftp.ebi.ac.uk/pub/databases/opentargets/platform/25.03/output/
(#388) — every column this adapter reads is present, unchanged, across all
four datasets. `_read_dataset()` still fails loudly (`MissingColumnsError`,
naming the exact columns) rather than silently dropping data if a future
release's schema differs.

Reads stream via `pyarrow.dataset.Dataset.to_batches()`, never
`.to_table().to_pylist()` (#385 — the same full-materialization bug that
caused PrimeKG's original OOM, see #374/commit 7d75756). `to_table()`
would build the entire dataset as one Arrow Table, then one Python list,
before a single row is yielded; `to_batches()` reads incrementally.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Iterator

import pyarrow.compute as pc
import pyarrow.dataset as ds


class MissingColumnsError(ValueError):
    """Raised when a dataset directory's parquet schema lacks a required column."""


_TARGET_COLUMNS = {"id", "approvedSymbol", "approvedName", "biotype"}
_DISEASE_COLUMNS = {"id", "name", "description"}
_MOLECULE_COLUMNS = {"id", "name", "drugType", "isApproved"}
_ASSOCIATION_COLUMNS = {"targetId", "diseaseId", "score"}

DEFAULT_MIN_ASSOCIATION_SCORE = 0.2

#: Rows per Arrow RecordBatch when streaming a dataset. Bounds how much of
#: any single dataset is held in memory at once, regardless of total file
#: size -- see the module docstring's #385 note.
DEFAULT_ARROW_BATCH_SIZE = 50_000


def _to_curie(raw_id: str, default_prefix: str) -> str:
    """Build a CURIE from a raw Open Targets id.

    OBO-style ontology ids embed their own prefix (`EFO_0000305`,
    `MONDO_0007254`) — split on the first underscore so the CURIE prefix
    matches the ontology the id actually came from, rather than assuming
    every disease id is EFO. Ids with no underscore (Ensembl gene ids,
    ChEMBL ids) fall back to `default_prefix`.
    """
    if "_" in raw_id:
        prefix, local = raw_id.split("_", 1)
        return f"{prefix}:{local}"
    return f"{default_prefix}:{raw_id}"


class OpenTargetsAdapter:
    """Adapter API contract: get_nodes() / get_edges() / schema_config / name."""

    name = "open-targets"
    schema_config = {
        "name": "open-targets",
        "prefixes": {
            "ENSEMBL": "https://identifiers.org/ensembl:",
            "EFO": "https://identifiers.org/efo:",
            "CHEMBL": "https://identifiers.org/chembl:",
        },
    }

    def __init__(
        self,
        path: str,
        min_association_score: float = DEFAULT_MIN_ASSOCIATION_SCORE,
        batch_size: int = DEFAULT_ARROW_BATCH_SIZE,
    ):
        self.datasets_dir = Path(path)
        self.min_association_score = min_association_score
        self.batch_size = batch_size

    def get_nodes(self) -> Iterator[tuple]:
        yield from self._target_nodes()
        yield from self._disease_nodes()
        yield from self._molecule_nodes()

    def get_edges(self) -> Iterator[tuple]:
        yield from self._association_edges()

    # -- dataset reading -----------------------------------------------------

    def _read_dataset(self, dirname: str, required_columns: set[str]):
        dir_path = self.datasets_dir / dirname
        if not dir_path.is_dir():
            raise FileNotFoundError(
                f"Open Targets dataset directory not found: {dir_path} "
                f"(expected a '{dirname}' subdirectory of {self.datasets_dir})"
            )
        dataset = ds.dataset(dir_path, format="parquet")
        missing = required_columns - set(dataset.schema.names)
        if missing:
            raise MissingColumnsError(
                f"{dirname} parquet schema is missing required column(s): "
                f"{sorted(missing)} (found: {sorted(dataset.schema.names)})"
            )
        return dataset

    # -- nodes -----------------------------------------------------------

    def _target_nodes(self) -> Iterable[tuple]:
        dataset = self._read_dataset("target", _TARGET_COLUMNS)
        for batch in dataset.to_batches(columns=sorted(_TARGET_COLUMNS), batch_size=self.batch_size):
            for row in batch.to_pylist():
                yield (
                    _to_curie(row["id"], "ENSEMBL"),
                    "Gene",
                    {
                        "symbol": row.get("approvedSymbol"),
                        "name": row.get("approvedName"),
                        "biotype": row.get("biotype"),
                    },
                    {"source": "open-targets", "dataset": "target"},
                    "EXTRACTED",
                    None,
                )

    def _disease_nodes(self) -> Iterable[tuple]:
        dataset = self._read_dataset("disease", _DISEASE_COLUMNS)
        for batch in dataset.to_batches(columns=sorted(_DISEASE_COLUMNS), batch_size=self.batch_size):
            for row in batch.to_pylist():
                yield (
                    _to_curie(row["id"], "EFO"),
                    "Disease",
                    {"name": row.get("name"), "description": row.get("description")},
                    {"source": "open-targets", "dataset": "disease"},
                    "EXTRACTED",
                    None,
                )

    def _molecule_nodes(self) -> Iterable[tuple]:
        dataset = self._read_dataset("drug_molecule", _MOLECULE_COLUMNS)
        for batch in dataset.to_batches(columns=sorted(_MOLECULE_COLUMNS), batch_size=self.batch_size):
            for row in batch.to_pylist():
                yield (
                    f"CHEMBL:{row['id']}",
                    "Drug",
                    {
                        "name": row.get("name"),
                        "drug_type": row.get("drugType"),
                        "is_approved": row.get("isApproved"),
                    },
                    {"source": "open-targets", "dataset": "drug_molecule"},
                    "EXTRACTED",
                    None,
                )

    # -- edges -----------------------------------------------------------

    def _association_edges(self) -> Iterable[tuple]:
        dataset = self._read_dataset("association_overall_direct", _ASSOCIATION_COLUMNS)
        score_filter = pc.field("score") >= self.min_association_score
        for batch in dataset.to_batches(
            columns=sorted(_ASSOCIATION_COLUMNS),
            filter=score_filter,
            batch_size=self.batch_size,
        ):
            for row in batch.to_pylist():
                yield (
                    "ASSOCIATED_WITH_DISEASE",
                    _to_curie(row["targetId"], "ENSEMBL"),
                    _to_curie(row["diseaseId"], "EFO"),
                    {"score": row["score"]},
                    {"source": "open-targets", "dataset": "association_overall_direct"},
                    "EXTRACTED",
                    None,
                )
