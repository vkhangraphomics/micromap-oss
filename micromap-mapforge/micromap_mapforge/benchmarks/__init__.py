"""Performance benchmarks for MapForge (#79 F5).

Three metrics, each scale-parameterized so an operator can run the headline
targets (resolve RSS @1M nodes, emit wall-time @100K rows, submit throughput)
or a quick smoke scale in CI. See ``README.md`` in this package.
"""
