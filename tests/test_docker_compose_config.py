"""Smoke checks on docker-compose.yml so a clean clone + `docker compose up`
yields a healthy API. Regression guard for GH#69."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"


def _api_env_list() -> list[str]:
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    return list(compose["services"]["micromap-api"]["environment"])


def test_api_service_passes_neo4j_database_env() -> None:
    """API container must receive NEO4J_DATABASE; otherwise it falls back to
    the in-code default 'graphomics' which Neo4j Community can't host."""
    env = _api_env_list()
    assert any(
        item.startswith("NEO4J_DATABASE=") for item in env
    ), f"micromap-api environment missing NEO4J_DATABASE; got: {env}"


def test_neo4j_database_default_is_neo4j_for_community() -> None:
    """The compose default must target the database Community Edition ships with."""
    env = _api_env_list()
    db_line = next(item for item in env if item.startswith("NEO4J_DATABASE="))
    assert db_line == "NEO4J_DATABASE=${NEO4J_DATABASE:-neo4j}", (
        f"expected neo4j default, got {db_line!r}"
    )
