# `contributor.yaml` Schema Reference

Field-by-field reference for the contributor manifest.

**JSON Schema:** [`micromap_mapforge/contributor/schema.json`](../../micromap_mapforge/contributor/schema.json) (Draft 2020-12, strict).

## Top-level shape

```yaml
contributor:  string      # required
tier:         enum        # required
sensitivity:  enum        # optional
```

`additionalProperties: false` — any field not listed below fails
validation. This is the tightest schema MapForge ships; the manifest is
small by design.

| Field | Type | Required | Constraint | Description |
|---|---|---|---|---|
| `contributor` | string | yes | min length 1 | Free-form identifier of the contributing organization or individual. Surfaces as `routing.yaml::provenance.contributor` and as the `id` property on the `:Organization` provenance node. |
| `tier` | enum | yes | `internal`, `partner`, `external` | Used by routing rules; see [`routing-policy-yaml.md#rulesmatch`](routing-policy-yaml.md#rulesmatch). |
| `sensitivity` | enum | no | `public`, `internal`, `pii` | Used by routing rules. Absent means the rule's `sensitivity` field never matches. |

---

## Worked example

```yaml
contributor: disbiome-curated-example
tier: partner
sensitivity: public
```

This is the shipped [`examples/disbiome/contributor.yaml`](../../../examples/disbiome/contributor.yaml).

---

## What is intentionally NOT in this schema

The strict-mode `additionalProperties: false` rejects everything outside
the three documented fields. Fields that are *easy to assume* but rejected:

| Field | Where it actually lives | Why not here |
|---|---|---|
| `organization_id` | `mapforge plan --organization-id <id>` CLI flag | The multi-tenant target tag is separable from the contributor identity. A single contributor org can write into multiple tenant tags. |
| `reviewer` | `mapforge approve --reviewer <name>` CLI flag | Reviewer identity is per-submission, not per-contributor. Recorded in the bundle's `manifest.json` and on the `:Reviewer` provenance node. |
| `email` | (not stored) | The manifest is for routing-rule evaluation only, not for record-keeping. Use the `description` field of `mapping.source` if you need source-level attribution. |
| `tier_reason` / `sensitivity_reason` | (not stored) | The schema is intentionally minimal; rationale belongs in the contributor's own documentation or commit messages. |

This was a real footgun during the #114 fix: my movies-domain example
shipped a contributor.yaml with `organization_id` and `reviewer` fields,
both of which the validator rejected at plan time. See
[#114](https://github.com/vkhangraphomics/MicroMap/issues/114) for the
fix.

---

## Validation behavior

Validator: `Draft202012Validator` with sorted error reporting (see
[`micromap_mapforge/contributor/validator.py`](../../micromap_mapforge/contributor/validator.py)).

### Common validation errors

| Error | Cause | Fix |
|---|---|---|
| `(root): 'contributor' is a required property` | Missing field. | Add a `contributor: <id>` entry. |
| `(root): 'tier' is a required property` | Missing field. | Add `tier: internal|partner|external`. |
| `tier: 'public' is not one of ['internal', 'partner', 'external']` | Wrong enum value. | Use one of the three valid tier values. |
| `(root): Additional properties are not allowed ('organization_id', 'reviewer' were unexpected)` | Extra fields the schema rejects. | See the [intentional-omissions table](#what-is-intentionally-not-in-this-schema) for where those fields actually live. |
| `sensitivity: 'critical' is not one of ['public', 'internal', 'pii']` | Wrong enum value. | Use one of the three valid sensitivity values, or omit the field. |

---

## Source-of-truth files

| What | Where |
|---|---|
| JSON Schema | [`micromap_mapforge/contributor/schema.json`](../../micromap_mapforge/contributor/schema.json) |
| Validator implementation | [`micromap_mapforge/contributor/validator.py`](../../micromap_mapforge/contributor/validator.py) |
| Production worked example | [`examples/disbiome/contributor.yaml`](../../../examples/disbiome/contributor.yaml) |

## Cross-references

- Where `contributor` ends up at submit time: [`../provenance.md`](../provenance.md)
- Routing rules that consume `tier`/`sensitivity`: [`routing-policy-yaml.md#rulesmatch`](routing-policy-yaml.md#rulesmatch)
- Authoring guide for the routing policy that uses this manifest: [`../routing-policy.md`](../routing-policy.md)
- Why some "obvious" fields are missing: [#114](https://github.com/vkhangraphomics/MicroMap/issues/114)
