"""Drive the official Neo4j MCP server (mcp-neo4j-cypher) over stdio and run
read Cypher against a Graphomics graph. Proof for issue #168.

Env: NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, NEO4J_DATABASE.
Run with a venv that has `mcp-neo4j-cypher` installed (its own deps; do NOT
share the micromap-mcp venv — fastmcp versions conflict).

NOTE (GAP 6): get_neo4j_schema is broken in 0.6.0 (passes None into
apoc.meta.schema). Until fixed, discover schema via read_neo4j_cypher with:
    CALL apoc.meta.schema({sample: 1000}) YIELD value RETURN value
or, APOC-free:
    CALL db.labels() YIELD label RETURN collect(label)
"""
import asyncio
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = os.environ.get("NEO4J_MCP_BIN", "mcp-neo4j-cypher")


def _text(result):
    return "\n".join(getattr(c, "text", str(c)) for c in result.content)


async def main():
    params = StdioServerParameters(
        command=SERVER, args=["--transport", "stdio"], env={**os.environ}
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            print("TOOLS:", [t.name for t in (await s.list_tools()).tools])
            # Schema discovery (GAP-6 workaround until get_neo4j_schema is fixed):
            print("\n-- schema (labels) --")
            print(_text(await s.call_tool("read_neo4j_cypher",
                  {"query": "CALL db.labels() YIELD label RETURN collect(label) AS labels"})))
            print("\n-- node counts by label (domain-agnostic) --")
            print(_text(await s.call_tool("read_neo4j_cypher",
                  {"query": "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY n DESC"})))


if __name__ == "__main__":
    asyncio.run(main())
