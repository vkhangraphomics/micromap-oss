"""Fabric federation proof for #168 phase 2.

Points the official Neo4j MCP server at the `graphomics` COMPOSITE database
and proves that a single Cypher query traverses both constituent subgraphs
(micromap: microbe→disease, primekg: disease↔drug/gene) — no bespoke REST,
no application-layer federation gateway.

Prerequisites:
  1. docker compose -f docker-compose.fabric.yml up -d
  2. docker compose -f docker-compose.fabric.yml exec graphomics-neo4j sh /setup.sh
  3. pip install mcp-neo4j-cypher   (own venv — fastmcp version conflict w/ micromap-mcp)

Run:
  NEO4J_URI=bolt://localhost:7690 NEO4J_USERNAME=neo4j \\
  NEO4J_PASSWORD=fabricpass123 NEO4J_DATABASE=graphomics \\
  python3 probe-fabric.py
"""

import asyncio
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = os.environ.get("NEO4J_MCP_BIN", "mcp-neo4j-cypher")


def _text(result) -> str:
    return "\n".join(getattr(c, "text", str(c)) for c in result.content)


async def main() -> None:
    params = StdioServerParameters(
        command=SERVER, args=["--transport", "stdio"], env={**os.environ}
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            # GAP 7: `db.labels()` unsupported on composite DB (by design — no direct
            # graph ops on the composite endpoint; must USE each constituent explicitly).
            print("=== 1. labels per constituent (composite forbids direct db.labels()) ===")
            print(_text(await s.call_tool("read_neo4j_cypher", {
                "query": (
                    "CALL { USE graphomics.micromap CALL db.labels() YIELD label "
                    "RETURN label, 'micromap' AS source } "
                    "RETURN label, source "
                    "UNION ALL "
                    "CALL { USE graphomics.primekg CALL db.labels() YIELD label "
                    "RETURN label, 'primekg' AS source } "
                    "RETURN label, source ORDER BY source, label"
                )
            })))

            print("\n=== 2. node counts by label across both subgraphs ===")
            print(_text(await s.call_tool("read_neo4j_cypher", {
                "query": (
                    "CALL { USE graphomics.micromap "
                    "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n } "
                    "RETURN label, n, 'micromap' AS source "
                    "UNION ALL "
                    "CALL { USE graphomics.primekg "
                    "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n } "
                    "RETURN label, n, 'primekg' AS source "
                    "ORDER BY source, n DESC"
                )
            })))

            print("\n=== 3. THE PROOF — single query: microbes + drugs for the same disease ===")
            print("    Agent asks: 'For IBD, what microbes are depleted AND what drugs are indicated?'")
            print("    Query spans micromap (microbe→disease) AND primekg (drug→disease)")
            print()
            # Pattern: each CALL block must be followed by its own RETURN before UNION ALL
            proof_q = (
                "CALL { USE graphomics.micromap "
                "MATCH (t:Taxon)-[r:ASSOCIATED_WITH_DISEASE]->(d:Disease) "
                "WHERE d.name_normalized = 'inflammatory bowel disease' AND r.direction = 'depleted' "
                "RETURN t.name AS entity, 'microbe (depleted)' AS entity_type, 'micromap' AS source } "
                "RETURN entity, entity_type, source "
                "UNION ALL "
                "CALL { USE graphomics.primekg "
                "MATCH (dr:Drug)-[:INDICATED_FOR]->(d:Disease) "
                "WHERE d.name_normalized = 'inflammatory bowel disease' "
                "RETURN dr.name AS entity, 'drug (indicated)' AS entity_type, 'primekg' AS source } "
                "RETURN entity, entity_type, source "
                "ORDER BY source, entity_type"
            )
            print(_text(await s.call_tool("read_neo4j_cypher", {"query": proof_q})))

            print("\n=== 4. shared Disease nodes — same name_normalized appears in both subgraphs ===")
            print(_text(await s.call_tool("read_neo4j_cypher", {
                "query": (
                    "CALL { USE graphomics.micromap "
                    "MATCH (d:Disease) RETURN d.name_normalized AS disease, count(*) AS n } "
                    "RETURN disease, n, 'micromap' AS source "
                    "UNION ALL "
                    "CALL { USE graphomics.primekg "
                    "MATCH (d:Disease) RETURN d.name_normalized AS disease, count(*) AS n } "
                    "RETURN disease, n, 'primekg' AS source "
                    "ORDER BY disease, source"
                )
            })))


if __name__ == "__main__":
    asyncio.run(main())
