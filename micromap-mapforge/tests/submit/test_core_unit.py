import json
from pathlib import Path
from unittest.mock import MagicMock

from micromap_mapforge.emit.bundle import IngestBundle
from micromap_mapforge.submit.core import MicroMapCoreExecutor


def _seed_bundle(tmp_path: Path) -> IngestBundle:
    root = tmp_path / "out"
    (root / "cypher").mkdir(parents=True)
    (root / "cypher" / "nodes_Taxon.cypher").write_text(
        "MERGE (n:Taxon {ncbi_tax_id: '562', organization_id: 'o'});\n", encoding="utf-8"
    )
    (root / "cypher" / "rels_X.cypher").write_text(
        "MATCH (a:Taxon {ncbi_tax_id: '562'}), (b:Disease {name: 'D'}) MERGE (a)-[r:X]->(b);\n",
        encoding="utf-8",
    )
    return IngestBundle(root=root)


def _mock_driver() -> MagicMock:
    driver = MagicMock()
    session_ctx = MagicMock()
    session = MagicMock()
    driver.session.return_value = session_ctx
    session_ctx.__enter__ = MagicMock(return_value=session)
    session_ctx.__exit__ = MagicMock(return_value=False)

    def execute_write(fn):
        tx = MagicMock()
        result = MagicMock()
        # Simulate counters: 1 node created per write, 0 on relationships
        result.consume.return_value = MagicMock(
            counters=MagicMock(nodes_created=1, relationships_created=0)
        )
        tx.run.return_value = result
        return fn(tx)

    session.execute_write = execute_write
    return driver


def test_submit_runs_cypher_files(tmp_path):
    bundle = _seed_bundle(tmp_path)
    driver = _mock_driver()
    executor = MicroMapCoreExecutor(driver=driver, database="neo4j")
    receipt = executor.submit(bundle)
    assert receipt.success is True
    assert receipt.destination == "micromap-core"


def test_dry_run_counts_statements_without_writing(tmp_path):
    bundle = _seed_bundle(tmp_path)
    driver = _mock_driver()
    executor = MicroMapCoreExecutor(driver=driver, database="neo4j")
    dry = executor.dry_run(bundle)
    # 1 node statement + 1 relationship statement in the seeded fixture
    assert dry.would_write_nodes == 1
    assert dry.would_write_relationships == 1
    # No actual session.execute_write called
    assert driver.session.call_count == 0


def _seed_batched_bundle(tmp_path: Path, n_rows: int) -> IngestBundle:
    root = tmp_path / "out"
    (root / "cypher").mkdir(parents=True)
    (root / "cypher" / "nodes_Drug.cypher").write_text(
        "UNWIND $batch_id AS row\n"
        "MERGE (n:Drug {id: row.merge_value, organization_id: $organization_id})\n"
        "SET n += row.props;\n",
        encoding="utf-8",
    )
    rows = [{"merge_value": f"D{i}", "props": {"name": f"drug-{i}"}} for i in range(n_rows)]
    params = {"organization_id": "org1", "batch_id": rows}
    (root / "cypher" / "nodes_Drug.params.json").write_text(json.dumps(params), encoding="utf-8")
    return IngestBundle(root=root)


def _recording_mock_driver() -> tuple[MagicMock, list[dict]]:
    """Like _mock_driver, but records every params dict passed to tx.run."""
    calls: list[dict] = []
    driver = MagicMock()
    session_ctx = MagicMock()
    session = MagicMock()
    driver.session.return_value = session_ctx
    session_ctx.__enter__ = MagicMock(return_value=session)
    session_ctx.__exit__ = MagicMock(return_value=False)

    def execute_write(fn):
        tx = MagicMock()

        def run(stmt, **params):
            calls.append(params)
            result = MagicMock()
            result.consume.return_value = MagicMock(
                counters=MagicMock(
                    nodes_created=len(params.get("batch_id", [1])),
                    relationships_created=0,
                )
            )
            return result

        tx.run = run
        return fn(tx)

    session.execute_write = execute_write
    return driver, calls


def test_submit_sends_small_batch_as_one_transaction(tmp_path):
    bundle = _seed_batched_bundle(tmp_path, n_rows=3)
    driver, calls = _recording_mock_driver()
    executor = MicroMapCoreExecutor(driver=driver, database="neo4j", chunk_size=10_000)
    receipt = executor.submit(bundle)

    assert len(calls) == 1
    assert len(calls[0]["batch_id"]) == 3
    assert calls[0]["organization_id"] == "org1"
    assert receipt.nodes_written == 3


def test_submit_splits_oversized_batch_into_chunks(tmp_path):
    bundle = _seed_batched_bundle(tmp_path, n_rows=5)
    driver, calls = _recording_mock_driver()
    executor = MicroMapCoreExecutor(driver=driver, database="neo4j", chunk_size=2)
    receipt = executor.submit(bundle)

    # 5 rows at chunk_size=2 -> chunks of 2, 2, 1
    assert [len(c["batch_id"]) for c in calls] == [2, 2, 1]
    # scalar params preserved unchanged on every chunk
    assert all(c["organization_id"] == "org1" for c in calls)
    # rows are a contiguous, non-overlapping partition of the original batch
    seen = [row["merge_value"] for c in calls for row in c["batch_id"]]
    assert seen == [f"D{i}" for i in range(5)]
    # counters summed across all chunks
    assert receipt.nodes_written == 5


def test_dry_run_count_unaffected_by_chunk_size(tmp_path):
    bundle = _seed_batched_bundle(tmp_path, n_rows=5)
    driver, _calls = _recording_mock_driver()
    executor = MicroMapCoreExecutor(driver=driver, database="neo4j", chunk_size=2)
    dry = executor.dry_run(bundle)
    assert dry.would_write_nodes == 5


# ---------------------------------------------------------------------------
# label-less cross-batch MATCH rewriting (#386)
# ---------------------------------------------------------------------------

def _seed_bundle_with_label_less_rel(tmp_path: Path) -> IngestBundle:
    root = tmp_path / "out"
    (root / "cypher").mkdir(parents=True)
    (root / "cypher" / "nodes_Anatomy.cypher").write_text(
        "UNWIND $batch_id AS row\n"
        "MERGE (n:Anatomy {id: row.merge_value, organization_id: $organization_id})\n"
        "SET n += row.props;\n",
        encoding="utf-8",
    )
    (root / "cypher" / "nodes_Anatomy.params.json").write_text(
        json.dumps({"organization_id": "org1", "batch_id": [{"merge_value": "A1", "props": {}}]}),
        encoding="utf-8",
    )
    (root / "cypher" / "rels_ANATOMY_PROTEIN_PRESENT.cypher").write_text(
        "UNWIND $batch_id__id AS row\n"
        "MATCH (a {id: row.from}),\n"
        "      (b {id: row.to})\n"
        "CREATE (a)-[r:ANATOMY_PROTEIN_PRESENT {organization_id: $organization_id}]->(b)\n"
        "SET r += row.props;\n",
        encoding="utf-8",
    )
    (root / "cypher" / "rels_ANATOMY_PROTEIN_PRESENT.params.json").write_text(
        json.dumps({
            "organization_id": "org1",
            "batch_id__id": [{"from": "A1", "to": "P1", "props": {}}],
        }),
        encoding="utf-8",
    )
    return IngestBundle(root=root)


def _seed_bundle_with_labeled_rel(tmp_path: Path) -> IngestBundle:
    root = tmp_path / "out"
    (root / "cypher").mkdir(parents=True)
    (root / "cypher" / "rels_ANATOMY_ANATOMY.cypher").write_text(
        "UNWIND $batch_id__id AS row\n"
        "MATCH (a:Anatomy {id: row.from}),\n"
        "      (b:Anatomy {id: row.to})\n"
        "CREATE (a)-[r:ANATOMY_ANATOMY {organization_id: $organization_id}]->(b)\n"
        "SET r += row.props;\n",
        encoding="utf-8",
    )
    (root / "cypher" / "rels_ANATOMY_ANATOMY.params.json").write_text(
        json.dumps({
            "organization_id": "org1",
            "batch_id__id": [{"from": "A1", "to": "A2", "props": {}}],
        }),
        encoding="utf-8",
    )
    return IngestBundle(root=root)


def _stmt_recording_mock_driver() -> tuple[MagicMock, list[str]]:
    """Records every raw statement text passed to tx.run (not just params)."""
    stmts_seen: list[str] = []
    driver = MagicMock()
    session_ctx = MagicMock()
    session = MagicMock()
    driver.session.return_value = session_ctx
    session_ctx.__enter__ = MagicMock(return_value=session)
    session_ctx.__exit__ = MagicMock(return_value=False)

    def execute_write(fn):
        tx = MagicMock()

        def run(stmt, **params):
            stmts_seen.append(stmt)
            result = MagicMock()
            result.consume.return_value = MagicMock(
                counters=MagicMock(nodes_created=0, relationships_created=0)
            )
            return result

        tx.run = run
        return fn(tx)

    session.execute_write = execute_write
    return driver, stmts_seen


def test_submit_rewrites_label_less_match_and_indexes_fallback_label(tmp_path):
    bundle = _seed_bundle_with_label_less_rel(tmp_path)
    driver, stmts = _stmt_recording_mock_driver()
    executor = MicroMapCoreExecutor(driver=driver, database="neo4j")
    executor.submit(bundle)

    # A fallback label was stamped onto every node and indexed.
    assert any(s.startswith("MATCH (n) SET n:") for s in stmts)
    assert any(s.startswith("CREATE INDEX") and "ON (n.id)" in s for s in stmts)

    # The original label-less pattern never reaches tx.run...
    assert not any("(a {id: row.from})" in s for s in stmts)
    # ...it was rewritten to reference the same fallback label.
    rel_stmts = [s for s in stmts if "ANATOMY_PROTEIN_PRESENT" in s]
    assert len(rel_stmts) == 1
    assert "(a:MapForgeNode {id: row.from})" in rel_stmts[0]
    assert "(b:MapForgeNode {id: row.to})" in rel_stmts[0]


def test_submit_leaves_labeled_match_statements_untouched(tmp_path):
    bundle = _seed_bundle_with_labeled_rel(tmp_path)
    driver, stmts = _stmt_recording_mock_driver()
    executor = MicroMapCoreExecutor(driver=driver, database="neo4j")
    executor.submit(bundle)

    # No label-less pattern present -> no fallback-label setup at all.
    assert not any(s.startswith("MATCH (n) SET n:") for s in stmts)
    assert not any(s.startswith("CREATE INDEX") for s in stmts)
    # The already-labeled statement is executed byte-for-byte unchanged.
    rel_stmts = [s for s in stmts if "ANATOMY_ANATOMY" in s]
    assert len(rel_stmts) == 1
    assert "(a:Anatomy {id: row.from})" in rel_stmts[0]


def test_submit_ensures_fallback_label_only_once_across_multiple_files(tmp_path):
    root = tmp_path / "out"
    (root / "cypher").mkdir(parents=True)
    for name in ("rels_AAA", "rels_BBB"):
        (root / "cypher" / f"{name}.cypher").write_text(
            "UNWIND $batch_id__id AS row\n"
            "MATCH (a {id: row.from}),\n"
            "      (b {id: row.to})\n"
            f"CREATE (a)-[r:{name[5:]} {{organization_id: $organization_id}}]->(b)\n"
            "SET r += row.props;\n",
            encoding="utf-8",
        )
        (root / "cypher" / f"{name}.params.json").write_text(
            json.dumps({
                "organization_id": "org1",
                "batch_id__id": [{"from": "X1", "to": "X2", "props": {}}],
            }),
            encoding="utf-8",
        )
    bundle = IngestBundle(root=root)
    driver, stmts = _stmt_recording_mock_driver()
    executor = MicroMapCoreExecutor(driver=driver, database="neo4j")
    executor.submit(bundle)

    assert sum(1 for s in stmts if s.startswith("MATCH (n) SET n:")) == 1
    assert sum(1 for s in stmts if s.startswith("CREATE INDEX")) == 1
