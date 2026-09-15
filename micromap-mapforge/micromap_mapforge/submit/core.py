"""MicroMapCoreExecutor — writes to local Neo4j with organization_id isolation.

Reads parameterized Cypher (per GH#71): each .cypher file has UNWIND-driven
MERGE statements that reference $params, paired with a sibling .params.json
that carries the bound values. The executor ships the params on session.run.

Large batches are split into multiple transactions (see `chunk_size`):
a single UNWIND transaction covering millions of rows risks exhausting the
server's heap, which on a shared Neo4j instance (Fabric composite serving
other constituents too) can destabilize more than just this write. Chunking
does not change the emitted Cypher — the same UNWIND statement just runs
once per chunk instead of once for the whole file.

Label-less cross-batch relationship endpoints (see `_FALLBACK_LABEL`, #386)
are rewritten here, at submit time, rather than at emission time in
serialize.py/streaming.py — those two files are shared with the tabular
7-stage flow, so stamping every bundle's nodes with an extra label there
would reach far more than the BioCypher import-kg case this exists for.
Rewriting only the statements that actually need it, only here, keeps the
fix scoped to submission and activates automatically without touching any
emitted bundle file.
"""

import json
import re
from pathlib import Path
from typing import Any, Iterator

from ..emit.bundle import IngestBundle
from .base import DryRunReport, SubmissionReceipt

#: Rows per transaction for a batched (UNWIND) statement. Chosen to keep a
#: single transaction's memory footprint small relative to a modest Neo4j
#: heap, while still writing enough rows per round-trip to stay fast.
DEFAULT_CHUNK_SIZE = 10_000

#: Stamped onto every node the first time a label-less cross-batch MATCH is
#: seen, so that MATCH can use an index instead of a full node-store scan.
#: A generic name is fine — this is a purely internal performance artifact,
#: never a domain label a caller would query for on its own.
_FALLBACK_LABEL = "MapForgeNode"

#: Matches the label-less cross-batch endpoint pattern serialize.py's
#: _write_rel_cypher / streaming.py's _resolve_match_clauses emit when a
#: relationship type's endpoints span more than one node label:
#: "(a {id: row.from})" or "(b {id: row.to})".
_LABEL_LESS_MATCH_RE = re.compile(r"\(([ab]) \{id: row\.(from|to)\}\)")


class MicroMapCoreExecutor:
    name = "micromap-core"

    def __init__(self, driver, database: str = "neo4j", chunk_size: int = DEFAULT_CHUNK_SIZE):
        self.driver = driver
        self.database = database
        self.chunk_size = chunk_size

    def _split_statements(self, text: str) -> list[str]:
        # Strip // line comments before splitting on ';' so multi-line UNWIND
        # statements aren't accidentally swallowed by a leading-comment chunk.
        lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("//")]
        cleaned = "\n".join(lines)
        return [s.strip() for s in cleaned.split(";") if s.strip()]

    def _load_params(self, cypher_path: Path) -> dict[str, Any]:
        params_path = cypher_path.with_suffix(".params.json")
        if not params_path.is_file():
            return {}
        return json.loads(params_path.read_text(encoding="utf-8"))

    def _iter_cypher(self, bundle: IngestBundle):
        cypher_dir = bundle.cypher_dir
        if not cypher_dir.is_dir():
            return
        for p in sorted(cypher_dir.iterdir()):
            if p.is_file() and p.suffix == ".cypher":
                yield p, self._split_statements(p.read_text(encoding="utf-8")), self._load_params(p)

    def _count_rows(self, params: dict[str, Any], stmts: list[str]) -> int:
        # Prefer batch sizes from params.json (the canonical post-refactor
        # shape: each batch is one row per node/rel that will be written).
        n = sum(
            len(v) for k, v in params.items()
            if k.startswith("batch_") and isinstance(v, list)
        )
        if n:
            return n
        # Legacy / hand-edited fallback: one MERGE statement = one write.
        return len(stmts)

    def dry_run(self, bundle: IngestBundle) -> DryRunReport:
        nodes = 0
        rels = 0
        for p, stmts, params in self._iter_cypher(bundle):
            count = self._count_rows(params, stmts)
            if p.name.startswith("nodes_"):
                nodes += count
            elif p.name.startswith("rels_"):
                rels += count
        return DryRunReport(
            destination=self.name,
            would_write_nodes=nodes,
            would_write_relationships=rels,
        )

    def _batch_keys(self, params: dict[str, Any]) -> list[str]:
        return [k for k, v in params.items() if k.startswith("batch_") and isinstance(v, list)]

    def _chunked_params(self, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Yield `params` split into transaction-sized chunks.

        Every real bundle carries exactly one batch_* list per file (one
        UNWIND variable per .cypher statement); chunk boundaries are taken
        from that list's length. Non-batch entries (e.g. organization_id)
        are scalars and are repeated on every chunk unchanged.
        """
        batch_keys = self._batch_keys(params)
        if not batch_keys:
            yield params  # legacy/hand-written statement with no batch param
            return
        total = len(params[batch_keys[0]])
        if total <= self.chunk_size:
            yield params
            return
        scalar_params = {k: v for k, v in params.items() if k not in batch_keys}
        for start in range(0, total, self.chunk_size):
            end = start + self.chunk_size
            chunk = dict(scalar_params)
            for key in batch_keys:
                chunk[key] = params[key][start:end]
            yield chunk

    def _ensure_fallback_label(self, session) -> None:
        """Stamp `_FALLBACK_LABEL` on every node currently in the database and
        index it. Idempotent (SET and CREATE INDEX IF NOT EXISTS are safe to
        repeat), and correct to run at any point after all node files have
        been processed — `_iter_cypher`'s `sorted()` order always puts
        `nodes_*` files before `rels_*` files, so by the time submit() hits
        the first relationship statement needing this, every node this
        bundle will create already exists."""
        session.execute_write(
            lambda tx: tx.run(f"MATCH (n) SET n:{_FALLBACK_LABEL}").consume()
        )
        session.execute_write(
            lambda tx: tx.run(
                f"CREATE INDEX IF NOT EXISTS FOR (n:{_FALLBACK_LABEL}) ON (n.id)"
            ).consume()
        )

    def _rewrite_label_less_match(self, stmt: str) -> str:
        """Point a label-less cross-batch MATCH endpoint at `_FALLBACK_LABEL`
        instead of no label at all, so it can use an index. Without this, a
        relationship type whose endpoints span more than one node label
        (serialize.py/streaming.py's CROSS_BATCH case — correct, since a
        single fixed label would silently miss reversed-direction or
        mixed-label rows) forces a full node-store scan per row: on PrimeKG
        this cratered throughput from ~11,000 rows/sec to ~67 rows/sec
        (#386)."""
        return _LABEL_LESS_MATCH_RE.sub(rf"(\1:{_FALLBACK_LABEL} {{id: row.\2}})", stmt)

    def submit(self, bundle: IngestBundle) -> SubmissionReceipt:
        nodes_written = 0
        rels_written = 0
        fallback_label_ensured = False
        with self.driver.session(database=self.database) as session:
            for _p, stmts, params in self._iter_cypher(bundle):
                for stmt in stmts:
                    if _LABEL_LESS_MATCH_RE.search(stmt):
                        if not fallback_label_ensured:
                            self._ensure_fallback_label(session)
                            fallback_label_ensured = True
                        stmt = self._rewrite_label_less_match(stmt)
                    for chunk_params in self._chunked_params(params):
                        counters = session.execute_write(
                            lambda tx, s=stmt, ps=chunk_params: tx.run(s, **ps).consume().counters
                        )
                        nodes_written += counters.nodes_created
                        rels_written += counters.relationships_created
        return SubmissionReceipt(
            destination=self.name,
            success=True,
            nodes_written=nodes_written,
            relationships_written=rels_written,
        )
