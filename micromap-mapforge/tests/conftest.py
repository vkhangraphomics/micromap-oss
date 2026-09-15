"""Shared pytest config for the MapForge test suite.

The `network` marker (registered in pyproject.toml) documents itself as
"skipped by default (run with -m network)". Registering a marker only
*documents* it, though — it does not make pytest deselect it. Without an
explicit skip hook, every `pytest tests/` run silently reaches out to live
public APIs (Zenodo/Figshare/Dryad/OSF), so a default run breaks whenever one
of those services rate-limits, requires auth, or rotates a record id — turning
an unrelated external outage into a red local suite.

`pytest_collection_modifyitems` enforces the contract: network tests run only
when the caller explicitly opts in with `-m network` (or names them on the
command line). This mirrors the canonical pattern in the pytest docs and keeps
the documented `pytest tests/` invocation green offline.
"""

import pytest


def pytest_collection_modifyitems(config, items):
    # If the caller explicitly asked for network tests (`-m network`, or any
    # marker expression mentioning `network`), honor it and skip nothing.
    marker_expr = config.getoption("-m", default="") or ""
    if "network" in marker_expr:
        return

    skip_network = pytest.mark.skip(
        reason="needs live network; opt in with `-m network`"
    )
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip_network)
