# Neo4j MCP — domain-agnostic graph consumption (issue #168)

Consume and traverse Graphomics graphs through the **official Neo4j MCP server**
(`mcp-neo4j-cypher`) via Cypher. Federate at the **graph layer** (Neo4j Fabric
composite databases). No bespoke REST API. No application-layer capability enum.

## Architecture decision (2026-06-07, agreed Vinith + Varun)

The REST federation gateway (`api/federation/`) has been **deleted** (PR #188).
`neo4j-mcp/` is now the primary graph consumption path.

```
Any agent (Claude Desktop/Code, OpenAI SDK, Codex, Nexus…)
    │  MCP (stdio or streamable-http)
    ▼
mcp-neo4j-cypher server
    │  Bolt
    ▼
graphomics COMPOSITE DATABASE  ◄── Neo4j Enterprise
    ├── graphomics.micromap    (microbe → disease)
    ├── graphomics.primekg     (disease ↔ drug / gene)
    └── graphomics.<any>       (add more via mapforge register-constituent)
```

## End-to-end workflow: build a federated graph from scratch

```bash
# 1. Prepare the instance (schema + indexes)
mapforge bootstrap --neo4j-uri bolt://<host>:7687 --neo4j-user neo4j --neo4j-password <pw>

# 2. Ingest data into a constituent database
mapforge import-kg adapter.py --source data.csv --organization-id myorg \
  --out bundle/ --neo4j-database micromap --neo4j-uri bolt://<host>:7687 \
  --neo4j-user neo4j --neo4j-password <pw>
# or: mapforge inspect → map → resolve → plan → emit → submit

# 3. Register the constituent with the Fabric composite
mapforge register-constituent \
  --composite graphomics \
  --name micromap \
  --database micromap \
  --neo4j-uri bolt://<host>:7690 \
  --neo4j-user neo4j --neo4j-password <pw>

# 4. Connect any agent to the composite via the Neo4j MCP
# (see connect/ for per-framework configs)

# 5. Agent traverses all constituents with one query
# "For IBD: depleted microbes + indicated drugs?"
# → Faecalibacterium prausnitzii (micromap) + Vedolizumab, Infliximab (primekg)
```

## Status (verified 2026-06-07, Neo4j Enterprise 5.26)

- ✅ **Phase 1** (PR #170): `read_neo4j_cypher` traversed MicroMap + Reactome subgraphs, single label-agnostic query spanning both — no bespoke route.
- ✅ **Phase 2** (PR #187): Fabric composite DB unioning `micromap` + `primekg`. One Cypher query returned microbe + drug data for IBD across both subgraphs via the Neo4j MCP. Proof in `fabric/probe-fabric.py`.
- ✅ **`mapforge register-constituent`** (this PR): CLI command to attach a freshly-ingested DB as a composite constituent — completes the from-scratch workflow.
- ⚠️ **GAP 6**: `get_neo4j_schema` broken in `mcp-neo4j-cypher` 0.6.0 (passes `None` to `apoc.meta.schema`). Workaround: use `CALL { USE <composite>.<db> CALL db.labels() YIELD label RETURN label, '<db>' AS source }`. Fix options: version pin or upstream patch.

## Quick verify (local, phase 1 — single DB)

```bash
python3 -m venv .venv && ./.venv/bin/pip install mcp-neo4j-cypher
NEO4J_URI=bolt://localhost:7687 NEO4J_USERNAME=neo4j NEO4J_PASSWORD=... \
NEO4J_DATABASE=neo4j python3 probe.py
```

## Fabric proof (phase 2 — composite DB)

```bash
cd fabric/
docker compose -f docker-compose.fabric.yml up -d
docker compose -f docker-compose.fabric.yml exec graphomics-neo4j sh /setup.sh
# then:
python3 -m venv .venv && ./.venv/bin/pip install mcp-neo4j-cypher
NEO4J_URI=bolt://localhost:7690 NEO4J_USERNAME=neo4j NEO4J_PASSWORD=fabricpass123 \
NEO4J_DATABASE=graphomics python3 fabric/probe-fabric.py
```

## Read-only / safety posture

- **Expose only `read_neo4j_cypher`** — do NOT expose `write_neo4j_cypher` to untrusted agents.
- On **Community**: no DB-level read-only user (RBAC is Enterprise-only) — the tool allowlist is the only control.
- On **Enterprise**: additionally bind a read-only RBAC role to the MCP's credentials as defence-in-depth.
- Add query guardrails (timeout, row cap) before exposing to untrusted callers.

## Enterprise requirement

Neo4j Fabric composite databases are **Enterprise-only**. Production deployment requires Neo4j Enterprise (AuraDB Enterprise or self-hosted with a valid license). The `docker-compose.fabric.yml` uses a 30-day eval license for dev/test only.

## Deprecation path

With MCP + Fabric in place:
- `api/federation/` (REST gateway, capability enum) → **delete** (issue #188)
- `api/routes/*` read endpoints → keep (used by `micromap-mcp/` + internal tooling)
- `mapforge` write path → keep unchanged
