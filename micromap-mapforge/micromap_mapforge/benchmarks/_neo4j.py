"""Neo4j provisioning + graph preload for the resolve/submit benchmarks (#79 F5).

`provision_driver()` uses `NEO4J_URI`/`NEO4J_USER`/`NEO4J_PASSWORD` if set
(point it at a real instance for the 1M-node target), otherwise spins a throwaway
testcontainer. `preload_taxa_diseases()` MERGEs N Taxon + N Disease nodes whose
identifier properties match the default schema_config so the resolver indexes them.
"""
from __future__ import annotations

import contextlib
import os
from typing import Iterator


def preload_taxa_diseases(driver, n: int, *, database: str = "neo4j",
                          org: str = "bench-org", batch: int = 5000) -> None:
    """MERGE N Taxon {ncbi_tax_id} + N Disease {mondo_id} nodes (ids matching
    ``synth.synth_rows`` so the resolve benchmark hits)."""
    query = (
        "UNWIND $rows AS r "
        "MERGE (t:Taxon {ncbi_tax_id: r.tax_id}) "
        "SET t.organization_id = $org, t.name = r.tax_name "
        "MERGE (d:Disease {mondo_id: r.mondo}) "
        "SET d.organization_id = $org, d.name = r.disease_name"
    )
    with driver.session(database=database) as session:
        for start in range(0, n, batch):
            rows = [
                {"tax_id": str(1_000_000 + i), "mondo": f"MONDO:{i:07d}",
                 "tax_name": f"Taxon {i}", "disease_name": f"Disease {i}"}
                for i in range(start, min(start + batch, n))
            ]
            session.run(query, rows=rows, org=org)


@contextlib.contextmanager
def provision_driver() -> Iterator[object]:
    """Yield a Neo4j driver: from env if NEO4J_URI is set, else a testcontainer."""
    from neo4j import GraphDatabase

    uri = os.environ.get("NEO4J_URI")
    if uri:
        drv = GraphDatabase.driver(
            uri,
            auth=(os.environ.get("NEO4J_USER", "neo4j"),
                  os.environ.get("NEO4J_PASSWORD", "")),
        )
        try:
            yield drv
        finally:
            drv.close()
        return

    from testcontainers.neo4j import Neo4jContainer
    with Neo4jContainer("neo4j:5.15") as n4j:
        drv = GraphDatabase.driver(n4j.get_connection_url(),
                                   auth=(n4j.username, n4j.password))
        try:
            yield drv
        finally:
            drv.close()
