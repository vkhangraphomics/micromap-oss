from pathlib import Path

import pytest

from api.federation_cypher_guard import DisallowedCypherError, validate_bundle_cypher


def _bundle_with_cypher(root: Path, text: str) -> Path:
    (root / "cypher").mkdir(parents=True)
    (root / "cypher" / "nodes_Taxon.cypher").write_text(text, encoding="utf-8")
    return root


def test_allows_emitted_shape(tmp_path: Path):
    root = _bundle_with_cypher(
        tmp_path / "b",
        "UNWIND $batch_0 AS row\nMERGE (n:Taxon {ncbi_tax_id: row.id})\nSET n.name = row.name;\n",
    )
    validate_bundle_cypher(root)  # no raise


def test_blocks_apoc(tmp_path: Path):
    root = _bundle_with_cypher(tmp_path / "b", "CALL apoc.load.json('http://evil') YIELD value RETURN value;\n")
    with pytest.raises(DisallowedCypherError):
        validate_bundle_cypher(root)


def test_blocks_load_csv(tmp_path: Path):
    root = _bundle_with_cypher(tmp_path / "b", "LOAD CSV FROM 'file:///etc/passwd' AS line RETURN line;\n")
    with pytest.raises(DisallowedCypherError):
        validate_bundle_cypher(root)


def test_blocks_drop_database(tmp_path: Path):
    root = _bundle_with_cypher(tmp_path / "b", "DROP DATABASE neo4j;\n")
    with pytest.raises(DisallowedCypherError):
        validate_bundle_cypher(root)


def test_validate_statement_allows_and_blocks():
    from api.federation_cypher_guard import validate_statement, DisallowedCypherError
    validate_statement("MATCH (n) RETURN n")          # ok
    validate_statement("MERGE (n:Taxon {id: 1})")     # ok
    for bad in ["CALL db.labels()", "LOAD CSV FROM 'x' AS l RETURN l",
                "MATCH (n) CALL apoc.do(n)", "DROP DATABASE neo4j",
                "MERGE (n) SET n.x = dbms.something()"]:
        with pytest.raises(DisallowedCypherError):
            validate_statement(bad)
