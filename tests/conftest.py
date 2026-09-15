"""
Shared pytest configuration and fixtures.
"""

import os
import pytest


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "integration: marks tests that require a live Neo4j instance",
    )
    config.addinivalue_line(
        "markers",
        "asyncio: marks tests that should run via pytest-asyncio",
    )
    config.addinivalue_line(
        "markers",
        "seeded: provisions its own throwaway service (testcontainers) and "
        "seeds the data it asserts on — needs no external box, so CI can run "
        "it. Selected by the repo-integration job (#298).",
    )


#: Credentials for the throwaway container. Fixed rather than random so the
#: NEO4J_* env below is reproducible and a failing run can be reproduced by hand.
_SEEDED_USER = "neo4j"
_SEEDED_PASSWORD = "testpassword"
_SEEDED_ENV_KEYS = ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "NEO4J_DATABASE")

#: The live-box settings as they were when pytest started, captured here at
#: conftest import — before any fixture can rewrite the environment.
#:
#: `seeded_neo4j_env` redirects NEO4J_* at a throwaway container for tests that
#: seed their own data. Without this snapshot, `neo4j_driver` would read those
#: redirected values and point the live-corpus tests at the EMPTY container:
#: `test_kg_integrity.py` asserts taxa are present, so it would fail outright
#: instead of skipping. Whether that happened would depend on which test ran
#: first — and `pytest-randomly` shuffles order by default, so it would fail
#: intermittently, which is the worst way for a test to be wrong.
_LIVE_NEO4J = {key: os.environ.get(key) for key in _SEEDED_ENV_KEYS}


@pytest.fixture(scope="session")
def seeded_neo4j_env():
    """Provision a throwaway Neo4j and point the `NEO4J_*` env at it (#307).

    For integration tests that **seed the data they assert on** and therefore
    need an empty database rather than the loaded corpus. Because every
    connection path in this repo reads `NEO4J_URI` / `NEO4J_USER` /
    `NEO4J_PASSWORD` / `NEO4J_DATABASE` lazily — including
    `integrations.neo4j_microbiome.get_microbiome_kg`, which the API routes call
    per request — redirecting the environment is enough; no test needs to learn
    a new connection API.

    Deliberately NOT autouse, and deliberately not wired into `neo4j_driver`
    below. `test_kg_integrity.py` asserts against a *loaded* graph ("taxa are
    present"), so pointing it at an empty container would turn correct skips
    into false failures. Live-corpus tests keep using `neo4j_driver` and skip
    when no box is reachable; self-seeding tests request this instead and run
    anywhere Docker does.

    Skips rather than fails when Docker or the package is missing — the same
    contract as `neo4j_driver`, so a laptop without Docker stays usable.
    """
    try:
        from testcontainers.neo4j import Neo4jContainer
    except ImportError:  # pragma: no cover - env-dependent
        pytest.skip("testcontainers[neo4j] not installed")

    try:
        container = Neo4jContainer(
            "neo4j:5.26", username=_SEEDED_USER, password=_SEEDED_PASSWORD,
        )
        with container:
            previous = {key: os.environ.get(key) for key in _SEEDED_ENV_KEYS}
            os.environ["NEO4J_URI"] = container.get_connection_url()
            os.environ["NEO4J_USER"] = _SEEDED_USER
            os.environ["NEO4J_PASSWORD"] = _SEEDED_PASSWORD
            # Community edition ships exactly one database, `neo4j`; the repo
            # default of `graphomics` requires Enterprise and would error here.
            os.environ["NEO4J_DATABASE"] = "neo4j"
            try:
                yield container
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"Could not start Neo4j container: {exc}")


@pytest.fixture(scope="session")
def neo4j_driver():
    """
    Create a Neo4j driver for integration tests.

    Reads connection details from environment variables with sensible defaults.
    Yields the driver and closes it after all tests complete.
    Skips the entire session if Neo4j is unreachable.
    """
    from neo4j import GraphDatabase
    from neo4j.exceptions import ServiceUnavailable, AuthError

    # From the pristine snapshot, NOT live os.environ — see _LIVE_NEO4J. This
    # fixture serves tests that need the LOADED corpus, so it must keep pointing
    # at the configured box even while `seeded_neo4j_env` has the environment
    # redirected at an empty container.
    uri = _LIVE_NEO4J["NEO4J_URI"] or "bolt://localhost:7687"
    user = _LIVE_NEO4J["NEO4J_USER"] or "neo4j"
    password = _LIVE_NEO4J["NEO4J_PASSWORD"] or "password"
    database = _LIVE_NEO4J["NEO4J_DATABASE"] or "graphomics"

    # Fast-fail timeouts so this fixture skips quickly when Neo4j is down
    # (defaults are 30s/60s and compound with retries to minutes) — keeps a
    # bare `pytest` run snappy instead of blocking before the skip.
    driver = GraphDatabase.driver(
        uri, auth=(user, password),
        connection_timeout=3, connection_acquisition_timeout=3,
        max_transaction_retry_time=3,
    )

    try:
        driver.verify_connectivity()
    except (ServiceUnavailable, AuthError, Exception) as exc:
        driver.close()
        pytest.skip(f"Neo4j is not available at {uri}: {exc}")

    # Attach database name so tests can use it
    driver._test_database = database

    yield driver

    driver.close()


@pytest.fixture(scope="session")
def neo4j_session(neo4j_driver):
    """Provide a Neo4j session for integration tests."""
    database = getattr(neo4j_driver, "_test_database", "graphomics")
    with neo4j_driver.session(database=database) as session:
        yield session
