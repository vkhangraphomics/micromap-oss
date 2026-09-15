"""Opt-in performance benchmarks (#79 F5).

Skipped unless ``RUN_BENCHMARKS=1`` so they never run in the normal suite. The
emit benchmark is offline; resolve/submit need Docker (testcontainers) and skip
cleanly without it. Thresholds are generous regression guards (catch a ~10x
regression, not an SLA) and every scale/threshold is env-overridable — see
``micromap_mapforge/benchmarks/README.md``.
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_BENCHMARKS"),
    reason="benchmarks are opt-in: set RUN_BENCHMARKS=1",
)


def _int_env(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _float_env(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


@pytest.fixture
def fresh_driver():
    """A throwaway Neo4j (fresh DB per test, so submit measures real creation)."""
    try:
        from testcontainers.neo4j import Neo4jContainer
    except ImportError:
        pytest.skip("testcontainers[neo4j] not installed")
    from neo4j import GraphDatabase
    try:
        with Neo4jContainer("neo4j:5.15") as n4j:
            drv = GraphDatabase.driver(n4j.get_connection_url(),
                                       auth=(n4j.username, n4j.password))
            yield drv
            drv.close()
    except Exception as e:  # pragma: no cover - env-dependent
        pytest.skip(f"Could not start Neo4j container: {e}")


def test_emit_benchmark():
    from micromap_mapforge.benchmarks.emit import bench_emit

    rows = _int_env("BENCH_EMIT_ROWS", 5000)
    r = bench_emit(rows)
    assert r["nodes"] == rows * 2 and r["edges"] == rows
    assert r["wall_s"] < _float_env("BENCH_EMIT_MAX_WALL_S", 30.0), r
    assert r["peak_rss_mb"] < _float_env("BENCH_EMIT_MAX_RSS_MB", 1500.0), r


def test_resolve_benchmark(fresh_driver):
    from micromap_mapforge.benchmarks.resolve import bench_resolve

    nodes = _int_env("BENCH_RESOLVE_NODES", 2000)
    rows = _int_env("BENCH_RESOLVE_ROWS", 1000)
    r = bench_resolve(fresh_driver, n_nodes=nodes, n_rows=rows)
    # ids are preloaded, so every row resolves both endpoints.
    assert r["resolved_count"] == rows * 2, r
    assert r["peak_rss_mb"] < _float_env("BENCH_RESOLVE_MAX_RSS_MB", 2000.0), r


def test_submit_benchmark(fresh_driver):
    from micromap_mapforge.benchmarks.submit import bench_submit

    rows = _int_env("BENCH_SUBMIT_ROWS", 1000)
    r = bench_submit(fresh_driver, n_rows=rows)
    assert r["nodes_written"] == rows * 2 and r["relationships_written"] == rows, r
    assert r["wall_s"] < _float_env("BENCH_SUBMIT_MAX_WALL_S", 120.0), r
