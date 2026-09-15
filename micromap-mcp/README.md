# MicroMap MCP

MCP server exposing MicroMap KG queries and MapForge ingestion to Nexus
agents. See [../docs/MICROMAP_MCP_DESIGN.md](../docs/MICROMAP_MCP_DESIGN.md)
for the design spec and [../docs/MICROMAP_MCP_PLAN.md](../docs/MICROMAP_MCP_PLAN.md)
for the implementation plan.

**Connecting an agent (Claude Code / Claude Desktop):** this is the canonical
authenticated HTTP endpoint for MicroMap (read + write in one connection) — see
[`connect/CONNECT.md`](connect/CONNECT.md).

## MapForge tools

10 tools covering the full ingest pipeline plus discipline discovery:

- `mapforge_create_bundle` — create a new bundle workspace
- `mapforge_inspect` — inspect a source file and return its schema profile
- `mapforge_templates_list` — list available discipline templates with version + description (sorted)
- `mapforge_templates_show` — fetch the YAML content of a named template (case-insensitive)
- `mapforge_map_heuristic` — draft a heuristic mapping.yaml from the source file; accepts optional `schema_config` (template name or path) to select discipline
- `mapforge_resolve` — resolve source entities against the knowledge graph
- `mapforge_plan` — write routing.yaml for the bundle
- `mapforge_emit` — generate Cypher statements
- `mapforge_approve` — mark the bundle approved
- `mapforge_submit` — execute the approved Cypher against Neo4j

## Dev

    pip install -e ".[dev]"
    pip install -e ../micromap-mapforge
    pytest
