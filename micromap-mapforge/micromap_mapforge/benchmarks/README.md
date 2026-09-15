# MapForge performance benchmarks (#79 F5)

Three scale-parameterized benchmarks that pin the metrics from MAC-41's
performance scope. They surface regressions and gate OSS release prep (MAC-56).

| Metric | Module | Headline target | What it measures |
|---|---|---|---|
| **Emit wall time** | `emit.py` | 100K source rows | `tabular_to_ir` + `serialize_bundle` wall time (offline) |
| **Resolve peak RSS** | `resolve.py` | 1M-node graph | peak process RSS while `build_resolvers` preloads the index + `resolve_mapping` runs |
| **Submit throughput** | `submit.py` | 100K rows | nodes+rels written per second via `MicroMapCoreExecutor` |

Measurement (`measure.py`) samples process RSS on a background thread, so
`peak_rss_mb` is the high-water mark *during* the run (not a before/after delta).

## Running

```bash
# Emit is offline — no Neo4j needed:
python -m micromap_mapforge.benchmarks.emit --rows 100000

# Resolve + submit use NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD if set, otherwise spin
# a throwaway testcontainer (needs Docker). Point at a real instance for the
# headline targets:
python -m micromap_mapforge.benchmarks.resolve --nodes 1000000 --rows 10000
python -m micromap_mapforge.benchmarks.submit  --rows 100000

# Smoke scale (throwaway container):
python -m micromap_mapforge.benchmarks.resolve --nodes 10000 --rows 5000
python -m micromap_mapforge.benchmarks.submit  --rows 5000
```

Each prints a JSON metrics dict.

## Baselines

Captured on a Windows dev box, Docker `neo4j:5.15`, Python 3.14 (indicative only —
these are regression guards, not SLAs):

| Metric | Scale | Result |
|---|---|---|
| emit | 100K rows (200K nodes + 100K edges) | 2.0 s · ~49K rows/s · peak 363 MB |
| resolve | 10K-node graph, 2K rows | 1.9 s · peak 207 MB (≈57 MB for the 10K-node index) |
| submit | 5K rows (15K writes) | 26.6 s · **~560 writes/s** · peak 160 MB |

**Observation — submit throughput is low** (~560 writes/s): `MicroMapCoreExecutor`
commits one transaction per Cypher statement. At the 1M-node target this is the
dominant cost; batching writes into fewer transactions is the obvious follow-up
optimization. The benchmark exists precisely to make this visible and to guard
against it getting worse.

## Regression-guard test

`tests/benchmarks/test_benchmarks.py` runs these at a small default scale, but is
**skipped unless `RUN_BENCHMARKS=1`** so it never runs in the normal suite. The
resolve/submit cases additionally skip without Docker.

```bash
RUN_BENCHMARKS=1 python -m pytest tests/benchmarks -v
```

Every scale and threshold is env-overridable, e.g.:

```bash
RUN_BENCHMARKS=1 BENCH_EMIT_ROWS=100000 BENCH_EMIT_MAX_WALL_S=10 \
  python -m pytest tests/benchmarks/test_benchmarks.py::test_emit_benchmark -v
```

| Env var | Default | Meaning |
|---|---|---|
| `BENCH_EMIT_ROWS` | 5000 | emit row count |
| `BENCH_EMIT_MAX_WALL_S` | 30 | emit wall-time ceiling |
| `BENCH_EMIT_MAX_RSS_MB` | 1500 | emit peak-RSS ceiling |
| `BENCH_RESOLVE_NODES` | 2000 | Taxon (and Disease) node count |
| `BENCH_RESOLVE_ROWS` | 1000 | rows to resolve |
| `BENCH_RESOLVE_MAX_RSS_MB` | 2000 | resolve peak-RSS ceiling |
| `BENCH_SUBMIT_ROWS` | 1000 | submit row count |
| `BENCH_SUBMIT_MAX_WALL_S` | 120 | submit wall-time ceiling |

Wiring these into CI (with tuned thresholds per runner) is gated on #60 (pytest-on-PR).
