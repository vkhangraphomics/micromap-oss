# `NewInstanceExecutor` + Fabric federation — Contract

## Status

> **Architecture shift (2026-06-07, agreed Vinith + Varun):**
> The REST federation gateway (`api/federation/`) is **superseded** by Neo4j
> Fabric composite databases + the Neo4j MCP server (issue #188 tracks deletion).
> The Bolt-write half of `NewInstanceExecutor` is **still valid**; the REST
> registration/probe half is replaced by `mapforge register-constituent`.
> See `neo4j-mcp/README.md` for the end-to-end workflow.

Original executor implemented as of issue #57 (built on federation v1, issue #34).
`mapforge register-constituent` added 2026-06-07 (issue #168 / #188).

## What it does (updated)

The `new-federated-instance` destination now performs **two** operations:

1. **Write the bundle's Cypher to the remote's Neo4j** via Bolt — unchanged.
   Reuses `MicroMapCoreExecutor` against a driver for the target database.
2. **Register the database as a Fabric constituent** via
   `mapforge register-constituent` — runs
   `CREATE ALIAS <composite>.<name> IF NOT EXISTS FOR DATABASE <db>`
   on the composite DB's system endpoint (idempotent).

~~Step 3 (REST probe via `POST /api/v1/admin/federation/sources/{id}/test`)~~
is removed — the composite DB is the probe; if agents can query it, it works.

## Wire model (updated)

- **Write path:** direct Bolt to the constituent Neo4j. The target database
  must accept the configured bolt user/password.
- **Registration:** DDL on the Enterprise Neo4j's `system` database via Bolt.
  No HTTP, no bearer tokens, no hub admin API.
- **Query path (new):** Neo4j MCP → Fabric composite DB → constituent DBs.
  One Cypher query, graph-layer fan-out. Domain-agnostic.

## End-to-end: build a federated graph from scratch

```bash
# 1. Bootstrap schema on the target DB
mapforge bootstrap \
  --neo4j-uri bolt://<host>:7687 --neo4j-user neo4j --neo4j-password <pw>

# 2. Ingest data (import-kg or inspect→map→resolve→plan→emit→submit)
mapforge import-kg adapter.py --source data/ --organization-id myorg \
  --out bundle/ --neo4j-database mydb \
  --neo4j-uri bolt://<host>:7687 --neo4j-user neo4j --neo4j-password <pw>

# 3. Register the constituent with the Fabric composite
mapforge register-constituent \
  --composite graphomics \
  --name mydb \
  --database mydb \
  --neo4j-uri bolt://<host>:7687 --neo4j-user neo4j --neo4j-password <pw>

# 4. Point the Neo4j MCP at the composite — agents now traverse all constituents
```

## Configuration (`routing-policy.yaml`)

> **Note:** The routing-policy schema and `NewInstanceExecutor` still require the
> full REST-federation fields (`source_id`, `base_url`, `auth_ref`, `capabilities`,
> etc.) until issue #188 (REST gateway deletion) is complete. The `composite`,
> `constituent`, and `database` fields shown in the ideal target below will cause
> schema-validation or key-error failures until the executor and schema are updated.
>
> **For now:** continue using the original routing-policy shape for `submit`, then
> call `mapforge register-constituent` as an explicit post-submit step to register
> the constituent with the Fabric composite.

**Current (working) routing-policy block for `new-federated-instance`:**

```yaml
destinations:
  new-federated-instance:
    federation:
      source_id:       mydb-source          # still required by the executor
      display_name:    "My DB"
      base_url:        https://placeholder.example.com  # required by schema
      bolt_uri:        bolt://neo4j:7687
      bolt_auth_ref:   env:MY_BOLT_PASSWORD
      auth_ref:        env:MY_FEDERATION_TOKEN          # required by schema
      auth_type:       bearer
      capabilities:    [search]
      organization_id: myorg
```

**Post-submit, register the constituent separately:**

```bash
mapforge register-constituent \
  --composite graphomics \
  --name mydb \
  --database mydb \
  --neo4j-uri bolt://<host>:7687 --neo4j-user neo4j --neo4j-password <pw>
```

**Target shape (once #188 lands and the executor is updated):**

```yaml
destinations:
  new-federated-instance:
    federation:
      composite:       graphomics
      constituent:     mydb
      database:        mydb
      bolt_uri:        bolt://neo4j:7687
      bolt_auth_ref:   env:MY_BOLT_PASSWORD
      organization_id: myorg
```

## Cross-references

| Artifact | Path |
|---|---|
| `mapforge register-constituent` CLI | `micromap_mapforge/cli.py` |
| Neo4j MCP + Fabric README | `neo4j-mcp/README.md` |
| Fabric proof script | `neo4j-mcp/fabric/probe-fabric.py` |
| REST gateway deprecation | issue #188 |
| MapForge design spec | `docs/superpowers/specs/2026-04-20-micromap-mapforge-design.md` |

## Provenance — two layers

MapForge writes a `:Contribution` node with confidence tags into the
**destination** Neo4j at submit time — that's *who put this data here*. The
federation gateway adds a `provenance.sources` field to query responses at
request time — that's *which deployments answered this query*. They're
complementary: a row that originated via MapForge into a federated instance
carries both.

The `Contribution` node lives in the remote's Neo4j (where the data is), not
at the hub. The federation registry record at the hub is the only hub-side
artifact about that contribution.

## Stand-alone curated KGs — provenance opt-out

MapForge ships with the Contribution writer enabled by default. For a
*stand-alone curated KG* — a Neo4j graph that has no MicroMap dependency and
does not want :Organization/:Reviewer/:Contribution nodes — set the opt-out in
`routing-policy.yaml`:

```yaml
provenance:
  enabled: false
```

When `false`:

- `mapforge submit` runs the bundle's Cypher against the configured Neo4j
  exactly as it would today.
- The Contribution writer is **not** invoked; no `:Organization`,
  `:Reviewer`, or `:Contribution` nodes are written.
- `--reviewer` is no longer required (the existing exit-8 pre-flight is
  skipped). If `--reviewer` is passed anyway, it is silently ignored with a
  one-line stderr note (`--reviewer ignored: provenance writer disabled by
  routing-policy`).
- `mapforge approve --reviewer <name>` still records the reviewer in the
  bundle's `manifest.json` for audit, but that field becomes dead data
  downstream.

The curated-KG owner is responsible for whatever provenance their domain
needs, using their own machinery.

### Destination naming for stand-alone KGs

The routing-policy `destination` enum (`micromap-core`, `registry-only`,
`new-federated-instance`) was named for the MicroMap-federation case.
Stand-alone curated KGs use `destination: micromap-core` — which in this code
path simply means "MapForge runs the bundle's Cypher directly against the
configured bolt URI." The name is historical and a bit misleading; renaming
or aliasing is deferred to issue #74 (schema as a first-class artifact).

### Forward-compat note

A bundle authored on new MapForge with `provenance: { enabled: false }` and
submitted with an *older* MapForge binary that predates this flag will still
have the Contribution writer fire (the old binary doesn't know about the
field). This is a self-inflicted downgrade scenario; we do not version-pin
bundles, so the only mitigation is to keep MapForge installs in sync.

### Constraint: non-MicroMap labels need `schema_adapter`

The opt-out turns off the **provenance writer** but does not relax the
**mapping schema**. The default strict mode of `mapping.schema.json` constrains
entity labels to the MicroMap ontology (`Taxon | Disease | Metabolite | Drug |
Gene | Protein | Pathway | Paper | BodySite`). A truly non-biomedical curated
KG (labels like `Movie`, `Asset`, `Customer`) cannot be ingested via the
default `inspect → map → resolve → plan → emit` flow with a CSV/TSV/JSON
source — the validator rejects the mapping.

The supported path for non-MicroMap labels today is `source.format:
schema_adapter` with a BioCypher adapter, run via `mapforge import-kg`. The
permissive entity-mapping branch in `mapping.schema.json` activates only for
that format.

Relaxing the strict-mode label enum so any format can carry any label is
tracked under issue #74 (Theme A — schema as a first-class artifact). Until
that lands, the opt-out's primary use case is small biomedical curated KGs
that want clean data without the `:Organization`/`:Reviewer`/`:Contribution`
audit trail.

## Failure modes

| Step fails with… | Receipt outcome | What it means |
|---|---|---|
| `SecretResolutionError` (env var unset) | exception (no remote touched) | fix `bolt_auth_ref`/`auth_ref` env, re-run |
| Bolt connect fails | `neo4j` exception | fix `bolt_uri` reachability or remote credentials |
| Cypher fails mid-bundle | `neo4j` exception, partial state on remote | same behavior as `MicroMapCoreExecutor` against local Neo4j |
| Admin POST (non-2xx, non-409) | `httpx.HTTPStatusError` | data is on remote; source not registered. Re-run is idempotent. |
| Admin PATCH after 409 fails | `httpx.HTTPStatusError` | as above |
| Probe returns `health.error` or non-200 | `success=False`; data + registration intact | check core ↔ remote network |
| Probe `sample_call.status="auth_failed"` | `success=False`; data + registration intact | fix the env var named in `auth_ref` |
| Probe `sample_call.skipped=true` | `success=True` | no shared capabilities with the probe sample table — benign |
