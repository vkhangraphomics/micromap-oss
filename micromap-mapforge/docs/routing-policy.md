# Routing Policy

A *routing policy* is a YAML file that decides which destination a bundle's
submit will write to. MapForge's planner reads `routing-policy.yaml` plus the
contributor's manifest plus optional CLI overrides and writes the resolved
choice into the bundle's `routing.yaml`.

**Audience:** an operator or external contributor authoring a policy file for
the first time. For field-by-field reference (every JSON Schema property,
every error message, every constraint), see
[`schemas/routing-policy-yaml.md`](schemas/routing-policy-yaml.md).

## TL;DR

The smallest viable policy is two lines:

```yaml
rules: []
default:
  destination: micromap-core
```

That says "send every bundle to `micromap-core`." For a bundle that's heading
to a federated instance instead, you also need a `destinations.new-federated-instance.federation` block (see [Federation destinations](#federation-destinations) below).

## File location

The policy file usually lives in one of three places:

- **Inside the bundle** (`<bundle>/routing-policy.yaml`) — typical for
  curated bundles where the policy is part of the contribution.
- **At the project root** — typical for organizations with a single shared
  policy across many bundles.
- **Anywhere on disk and passed explicitly** via `mapforge plan --policy <path>`.

The shipped example at
[`routing-policy.example.yaml`](../routing-policy.example.yaml) is a
fully-worked policy with all five-tier rules.

## Top-level shape

```yaml
provenance:                          # optional; see provenance.md
  enabled: false

rules:                               # required; may be empty list
  - match: { ... }
    destination: <name>

default:                             # required
  destination: <name>

destinations:                        # required only for new-federated-instance
  new-federated-instance:
    federation: { ... }
```

Schema:
[`micromap_mapforge/emit/routing_policy.schema.json`](../micromap_mapforge/emit/routing_policy.schema.json).
Strict `additionalProperties: false` at every level.

## Decision precedence

The planner picks the destination in this order:

1. **`--destination` CLI flag** (explicit override, highest precedence).
2. **First matching rule** in `rules[]`, in declaration order.
3. **`default.destination`** when no rule matches.

A "rule match" requires *every* field in the rule's `match` block to match
the contributor's manifest + bundle metadata. An empty `match: {}` matches
everything (a useful pattern for catchall rules placed before `default`).

## Match conditions

| Field | Type | Source | Example |
|---|---|---|---|
| `tier` | enum: `internal | partner | external` | `contributor.yaml::tier` | `tier: internal` |
| `sensitivity` | enum: `public | internal | pii` | `contributor.yaml::sensitivity` | `sensitivity: pii` |
| `min_rows` | integer ≥ 0 | `--row-count` flag | `min_rows: 100000` |
| `max_rows` | integer ≥ 0 | `--row-count` flag | `max_rows: 99999` |

Any subset of fields is allowed. Unknown fields cause schema validation to fail.

**Row-count rules** require you to pass `--row-count <N>` to `mapforge plan`.
The planner doesn't auto-count the source file — that decision is left to the
operator (some sources are streamed, some are pre-counted, etc.).

## Destinations

| Destination | Meaning |
|---|---|
| `micromap-core` | Write the bundle's Cypher directly to the bolt URI you supply at submit time. The simplest path. |
| `registry-only` | Don't write any data nodes. Only register the source on the central federation registry. Useful for federated sources that own their loader path. |
| `new-federated-instance` | Write the data to a *remote* Neo4j (federated peer), then register the source on micromap-core. See the [Federation destinations](#federation-destinations) section. |

## Provenance block

```yaml
provenance:
  enabled: false      # default true; set to false for stand-alone curated KGs
```

When `enabled: false`, submit skips the Contribution writer (the
`:Organization` / `:Reviewer` / `:Contribution` nodes) and no longer requires
`--reviewer`. See [`provenance.md`](provenance.md) and
[`new-instance-executor-contract.md`](new-instance-executor-contract.md)
§"Stand-alone curated KGs" for the full picture.

## Federation destinations

When **any** rule (or `default`) routes to `new-federated-instance`, the
policy must also include a `destinations.new-federated-instance.federation`
block:

```yaml
destinations:
  new-federated-instance:
    federation:
      source_id:       acme-pharma            # globally unique on micromap-core's registry
      display_name:    "Acme Pharma"
      base_url:        https://micromap.acme.example.com
      bolt_uri:        bolt+s://acme-neo4j.acme.example.com:7687
      bolt_auth_ref:   env:ACME_BOLT_PASSWORD # MapForge → remote bolt password
      auth_ref:        env:ACME_FEDERATION_TOKEN  # hub → remote HTTP queries
      auth_type:       bearer                 # or api_key_header
      capabilities:    [diseases.taxa, taxa.diseases]
      organization_id: acme
      timeout_ms:      5000                   # optional override
```

**Secret resolution:** `bolt_auth_ref` and `auth_ref` use the `env:NAME`
indirection contract — the referenced env var must be set on the MapForge
process at submit time, otherwise submit fails with `SecretResolutionError`
before touching the remote.

**Capabilities enum** matches the routes decorated with `@federated` on
micromap-core. Currently: `diseases.taxa`, `diseases.metabolites`,
`taxa.diseases`, `search`. The list expands as more routes are decorated.

For the full federation contract — write path, registration, probe outcomes,
failure modes — see
[`new-instance-executor-contract.md`](new-instance-executor-contract.md).

## `contributor.yaml`

A small companion file the planner reads to evaluate rule conditions.

```yaml
contributor: acme-pharma         # required; identifier of the contributing org
tier: partner                    # required; internal | partner | external
sensitivity: internal            # optional; public | internal | pii
```

Schema:
[`micromap_mapforge/contributor/schema.json`](../micromap_mapforge/contributor/schema.json).
Strict `additionalProperties: false` — fields not in this list will be rejected.

**Common pitfall:** adding fields the schema doesn't allow (e.g.,
`organization_id`, `reviewer`) causes the planner to exit 2. The
`organization_id` is supplied via `--organization-id` at plan time, and the
reviewer is supplied via `mapforge approve --reviewer`. The manifest is just
for routing-rule evaluation.

## Worked examples

### Example 1: simplest possible

```yaml
# routing-policy.yaml
rules: []
default:
  destination: micromap-core
```

Every bundle goes to `micromap-core`. No contributor.yaml fields are consulted.
Suitable for a single-tenant local setup.

### Example 2: tier-based with PII guardrail

```yaml
rules:
  - match: { sensitivity: pii }       # PII never lands in the main graph
    destination: registry-only

  - match: { tier: internal }
    destination: micromap-core

  - match: { tier: partner }
    destination: micromap-core

default:
  destination: registry-only          # external contributors don't get write access by default
```

### Example 3: size-based federation

```yaml
rules:
  - match: { sensitivity: pii }
    destination: registry-only

  - match: { tier: internal }
    destination: micromap-core

  - match: { tier: partner, max_rows: 99999 }
    destination: micromap-core           # small partner bundles → central

  - match: { tier: partner, min_rows: 100000 }
    destination: new-federated-instance  # large partner bundles → federated peer

  - match: { tier: external }
    destination: registry-only

default:
  destination: registry-only

destinations:
  new-federated-instance:
    federation:
      source_id:       acme-pharma
      display_name:    "Acme Pharma"
      base_url:        https://micromap.acme.example.com
      bolt_uri:        bolt+s://acme-neo4j.acme.example.com:7687
      bolt_auth_ref:   env:ACME_BOLT_PASSWORD
      auth_ref:        env:ACME_FEDERATION_TOKEN
      capabilities:    [diseases.taxa, taxa.diseases]
      organization_id: acme
```

This is the shipped
[`routing-policy.example.yaml`](../routing-policy.example.yaml). It encodes a
five-tier governance model: internal small or large goes to core, partner
small to core, partner large to a federated peer, external + anything PII goes
to registry-only.

### Example 4: provenance opt-out for a curated standalone KG

```yaml
provenance:
  enabled: false

rules: []
default:
  destination: micromap-core    # name is historical; here it just means "direct bolt"
```

No `:Organization` / `:Reviewer` / `:Contribution` nodes are written. The
curated KG owner is responsible for any provenance they want, using their own
machinery.

### Example 5: per-source override at the CLI

If you have a single policy but want to one-off a specific bundle, skip the
rules entirely:

```bash
mapforge plan --bundle ./my-bundle \
    --organization-id urgent-curation \
    --destination registry-only      # override → wins over any policy rule
```

## Common errors

| Symptom | Cause |
|---|---|
| `routing policy invalid: rules[0].match: additional properties are not allowed ('source_type')` | A typo in a match field. Match fields are limited to `tier`, `sensitivity`, `min_rows`, `max_rows`. |
| `routing matched destination 'new-federated-instance' but policy is missing destinations.new-federated-instance.federation` | A rule (or default) routes to `new-federated-instance` but the destinations block is absent. Add the `destinations` block. |
| `contributor invalid: (root): additional properties are not allowed ('organization_id', 'reviewer' were unexpected)` | The contributor manifest carries fields outside the schema. See the [contributor.yaml](#contributoryaml) section. |
| `provenance: enabled is required` | The policy declares a `provenance` block but no `enabled` key. Either drop the empty block or set `enabled: true`/`false`. |

## Cross-references

- Federation contract (the executor end of the `new-federated-instance` path): [`new-instance-executor-contract.md`](new-instance-executor-contract.md)
- Provenance writer (what `provenance.enabled` controls): [`provenance.md`](provenance.md)
- Bundle output (where `routing.yaml` ends up): [`bundle-anatomy.md#routingyaml`](bundle-anatomy.md#routingyaml)
- Walkthrough (where `plan` fits in the 7-stage flow): [`contributor-flow.md#stage-4--plan`](contributor-flow.md#stage-4--plan)
