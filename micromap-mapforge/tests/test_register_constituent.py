"""CLI: mapforge register-constituent — unit tests (no live Neo4j)."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from micromap_mapforge.cli import main


def _mock_driver(constituents: list[str]):
    """Return a patched GraphDatabase.driver whose system session returns
    the given constituents list for SHOW DATABASES."""
    record = MagicMock()
    record.__getitem__ = lambda self, key: constituents if key == "constituents" else None

    result = MagicMock()
    result.single.return_value = record

    session = MagicMock()
    session.__enter__ = lambda s: s
    session.__exit__ = MagicMock(return_value=False)
    session.run.return_value = result

    driver = MagicMock()
    driver.session.return_value = session
    driver.close = MagicMock()
    return driver


def test_register_constituent_success():
    driver = _mock_driver(["graphomics.micromap", "graphomics.primekg"])
    with patch("neo4j.GraphDatabase.driver", return_value=driver):
        result = CliRunner().invoke(main, [
            "register-constituent",
            "--composite", "graphomics",
            "--name", "primekg",
            "--database", "primekg",
            "--neo4j-uri", "bolt://localhost:7690",
            "--neo4j-user", "neo4j",
            "--neo4j-password", "pass",
        ])
    assert result.exit_code == 0, result.output
    assert "graphomics.primekg" in result.output
    assert "primekg" in result.output
    # Verify the alias DDL was issued on the system database, using the
    # namespaced `composite`.`name` form (NOT a single quoted `composite.name`,
    # which would create a standalone alias instead of a constituent).
    driver.session.assert_called_once_with(database="system")
    cypher_calls = [str(c) for c in driver.session.return_value.run.call_args_list]
    assert any("`graphomics`.`primekg`" in c for c in cypher_calls)
    assert not any("`graphomics.primekg`" in c for c in cypher_calls)


def test_register_constituent_composite_not_found():
    driver = _mock_driver([])
    driver.session.return_value.run.return_value.single.return_value = None
    with patch("neo4j.GraphDatabase.driver", return_value=driver):
        result = CliRunner().invoke(main, [
            "register-constituent",
            "--composite", "does-not-exist",
            "--name", "x",
            "--database", "x",
            "--neo4j-uri", "bolt://localhost",
            "--neo4j-user", "u",
            "--neo4j-password", "p",
        ])
    assert result.exit_code != 0
    assert "not found" in result.output.lower()


def test_register_constituent_alias_missing_after_creation():
    """Alias created but not reflected in SHOW DATABASES → fail loud."""
    driver = _mock_driver(["graphomics.micromap"])  # primekg missing
    with patch("neo4j.GraphDatabase.driver", return_value=driver):
        result = CliRunner().invoke(main, [
            "register-constituent",
            "--composite", "graphomics",
            "--name", "primekg",
            "--database", "primekg",
            "--neo4j-uri", "bolt://localhost",
            "--neo4j-user", "u",
            "--neo4j-password", "p",
        ])
    assert result.exit_code != 0
    assert "not found" in result.output.lower() or "missing" in result.output.lower()


def test_register_constituent_driver_closed_on_error():
    """Driver is always closed, even when the session raises."""
    driver = MagicMock()
    driver.session.side_effect = RuntimeError("connection refused")
    with patch("neo4j.GraphDatabase.driver", return_value=driver):
        result = CliRunner().invoke(main, [
            "register-constituent",
            "--composite", "g", "--name", "n", "--database", "d",
            "--neo4j-uri", "bolt://localhost", "--neo4j-user", "u", "--neo4j-password", "p",
        ])
    driver.close.assert_called_once()
    assert result.exit_code != 0
