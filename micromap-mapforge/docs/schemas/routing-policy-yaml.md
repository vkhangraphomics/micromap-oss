# `routing-policy.yaml` Schema Reference

Field-by-field reference for the routing-policy file. For the authoring
guide (worked examples, decision precedence, common workflows), see
[`../routing-policy.md`](../routing-policy.md).

**JSON Schema:** [`micromap_mapforge/emit/routing_policy.schema.json`](../../micromap_mapforge/emit/routing_policy.schema.json) (Draft 2020-12, strict).

## Top-level shape

```yaml
rules:        [ ... ]      # required, may be empty
default:      { ... }      # required
provenance:   { ... }      # optional
destinations: { ... }      # required iff any rule/default targets new-federated-instance
```

`additionalProperties: false` at every level.

| Field | Type | Required | Description |
|---|---|---|---|
| `rules` | array | yes | Ordered list of routing rules. First match wins. Empty list (`[]`) is valid. |
| `default` | object | yes | Fallback when no rule matches. |
| `provenance` | object | no | When present, controls whether the Contribution writer fires at submit time. |
| `destinations` | object | conditionally | Required only when at least one rule or `default` targets `new-federated-instance`. |

---

## `rules[]`

Each rule is an object with `match` (the condition) and `destination` (the
outcome).

```yaml
rules:
  - match:        { ... }     # required
    destination:  enum        # required
```

### `rules[].match`

| Field | Type | Constraint | Source |
|---|---|---|---|
| `tier` | enum | `internal`, `partner`, `external` | `contributor.yaml::tier` |
| `sensitivity` | enum | `public`, `internal`, `pii` | `contributor.yaml::sensitivity` |
| `min_rows` | integer | ≥ 0 | `--row-count <N>` CLI flag |
| `max_rows` | integer | ≥ 0 | `--row-count <N>` CLI flag |

All fields optional; **a rule matches when every present field matches**.
An empty `match: {}` matches everything (a useful catch-all pattern).

Unknown keys in `match` fail validation (`additionalProperties: false`).

### `rules[].destination`

Required enum: `micromap-core | registry-only | new-federated-instance`.
See [`destinations`](#destinations) below for the federation config that
`new-federated-instance` requires.

---

## `default`

```yaml
default:
  destination: enum     # required, same enum as rules[].destination
```

Used when no rule matches. Cannot be omitted — the schema requires
explicit handling of the catch-all case.

---

## `provenance` (optional)

Controls the Contribution writer behavior. **When absent, defaults to
enabled** — preserving the pre-#63 behavior.

```yaml
provenance:
  enabled: bool       # required if `provenance` block is present
```

| Field | Type | Required | Description |
|---|---|---|---|
| `enabled` | boolean | yes (if block present) | `false` disables the writer for this project. See [`../provenance.md`](../provenance.md). |

**Edge cases the schema rejects:**

- `provenance: {}` → `enabled is required` (the empty block can't silently
  mean enabled-via-default; the schema forces explicit choice).
- `provenance: { enabled: "no" }` → must be a boolean.
- `provenance: { enabled: false, extra: 1 }` → `additionalProperties: false`.

The flag is **frozen at emit time**: `plan_route()` copies it into the
bundle's `routing.yaml::provenance.enabled`, and submit reads from there.
A re-emit after editing the policy applies the new value.

---

## `destinations`

Required only when at least one rule/default routes to
`new-federated-instance`. Carries the per-destination config the executor
needs.

```yaml
destinations:
  new-federated-instance:
    federation:
      source_id:       string         # required
      display_name:    string         # required
      base_url:        uri            # required
      bolt_uri:        string         # required
      bolt_user:       string         # optional, defaults to 'mapforge'
      bolt_auth_ref:   "env:NAME"     # required
      auth_ref:        "env:NAME"     # required
      auth_type:       enum           # optional
      auth_header_name: string        # optional
      capabilities:    [ enum, ... ]  # required, ≥1
      organization_id: string         # required
      tier:            enum           # optional
      timeout_ms:      integer        # optional
```

| Field | Type | Constraint | Description |
|---|---|---|---|
| `source_id` | string | pattern `^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$` | Globally unique on the federation registry. Stored on the `:FederatedSource` node and used as the registry's primary key. |
| `display_name` | string | non-empty | Human-readable display name shown in the registry browser. |
| `base_url` | string | URI format | Base HTTP URL of the remote MicroMap (the hub queries this for federated reads). |
| `bolt_uri` | string | non-empty | Bolt URL of the remote's Neo4j (MapForge writes the bundle here). |
| `bolt_user` | string | optional, min length 1 | Defaults to `mapforge` per the executor contract. |
| `bolt_auth_ref` | string | pattern `^env:[A-Z][A-Z0-9_]*$` | Env-var indirection — the actual password is read from this env var at submit time. |
| `auth_ref` | string | pattern `^env:[A-Z][A-Z0-9_]*$` | Same indirection; this is for the hub → remote HTTP queries. |
| `auth_type` | enum | `bearer`, `api_key_header` | Optional; how the hub authenticates to the remote. |
| `auth_header_name` | string | optional | Required when `auth_type: api_key_header`. |
| `capabilities` | array of enum | ≥1; enum: `diseases.taxa`, `diseases.metabolites`, `taxa.diseases`, `search` | Which `@federated`-decorated routes the remote responds to. |
| `organization_id` | string | non-empty | Multi-tenant tag stamped on the contributor's nodes. |
| `tier` | enum | `micromap`, `adapter` | Optional; `micromap` (homogeneous peer) vs `adapter` (Tier 1/2, deferred). |
| `timeout_ms` | integer | ≥ 100 | Optional; per-call HTTP timeout for the hub → remote queries. |

**Secret indirection pattern:** `bolt_auth_ref` and `auth_ref` must take
the form `env:VARIABLE_NAME`. At submit time, MapForge resolves the env
var to the actual secret. The pattern is enforced syntactically — typos
like `ACME_BOLT_PASSWORD` (no `env:` prefix) are rejected at policy-load
time, not submit time.

**Capabilities enum:** matches the routes on micromap-core decorated
with `@federated` in `api/`. The list expands as more routes are
federation-capable. Adding a new capability to a policy without
deploying the corresponding decoration on the peer means the probe gate
will report `auth_failed` / no-shared-capabilities.

For the full federation runtime contract — write path, registration,
probe outcomes, failure modes — see [`../new-instance-executor-contract.md`](../new-instance-executor-contract.md).

---

## Decision precedence

The planner picks the destination at `plan` time in this order:

1. **`--destination` CLI flag** (explicit override, highest precedence).
2. **First matching rule** in `rules[]`, in declaration order.
3. **`default.destination`** when no rule matches.

This is enforced in [`micromap_mapforge/emit/routing.py::plan_route`](../../micromap_mapforge/emit/routing.py).

---

## Validation behavior

Validator: `Draft202012Validator` with sorted error reporting (see
[`micromap_mapforge/emit/routing_policy.py`](../../micromap_mapforge/emit/routing_policy.py)).

### Common validation errors

| Error | Cause | Fix |
|---|---|---|
| `(root): 'rules' is a required property` | Missing top-level `rules`. | Add `rules: []` if you have no rules; `rules` cannot be omitted. |
| `(root): 'default' is a required property` | Missing top-level `default`. | Add `default: { destination: <name> }`. |
| `rules.N.match: Additional properties are not allowed ('source_type' was unexpected)` | Typo in a match field. | Match fields are limited to `tier`, `sensitivity`, `min_rows`, `max_rows`. |
| `rules.N.destination: 'cloud' is not one of [...]` | Unknown destination. | Use one of the three enum values. |
| `provenance: 'enabled' is a required property` | Empty `provenance: {}` block. | Either drop the block or set `enabled: true/false`. |
| `destinations.new-federated-instance.federation.source_id: '<value>' does not match '^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$'` | Uppercase letters, leading/trailing hyphens, or > 64 chars in source_id. | Use kebab-case lowercase, 2-64 chars. |
| `destinations.new-federated-instance.federation.bolt_auth_ref: 'ACME_PASSWORD' does not match '^env:[A-Z][A-Z0-9_]*$'` | Missing the `env:` prefix or lowercase var name. | Use `env:ACME_PASSWORD` form. |

The runtime planner *also* raises if `default` or any rule routes to
`new-federated-instance` but the `destinations.new-federated-instance.federation`
block is absent — that's a referential-integrity check the JSON Schema can't
express alone.

---

## Source-of-truth files

| What | Where |
|---|---|
| JSON Schema | [`micromap_mapforge/emit/routing_policy.schema.json`](../../micromap_mapforge/emit/routing_policy.schema.json) |
| Validator implementation | [`micromap_mapforge/emit/routing_policy.py`](../../micromap_mapforge/emit/routing_policy.py) |
| Planner that consumes the policy | [`micromap_mapforge/emit/routing.py`](../../micromap_mapforge/emit/routing.py) |
| Production worked example | [`micromap-mapforge/routing-policy.example.yaml`](../../routing-policy.example.yaml) |

## Cross-references

- Authoring guide (worked examples + common workflows): [`../routing-policy.md`](../routing-policy.md)
- Provenance opt-out (the `provenance` block): [`../provenance.md`](../provenance.md)
- Federation contract (the `destinations.new-federated-instance.federation` block): [`../new-instance-executor-contract.md`](../new-instance-executor-contract.md)
- Contributor manifest (the source of `tier`/`sensitivity` for rule matching): [`contributor-yaml.md`](contributor-yaml.md)
