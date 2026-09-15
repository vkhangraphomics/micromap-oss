# `normalize.py` Reference

Reference for the two normalization helpers MapForge ships at
[`micromap_mapforge/normalize.py`](../../micromap_mapforge/normalize.py).
These are the canonical algorithms downstream contributors should
implement against when authoring data that's expected to MERGE with
MicroMap-shaped graphs.

**Why this matters:** when a `mapping.yaml` declares
`normalizer: normalize_disease_name`, the resolver applies this exact
function to source values before lookup. A separately-implemented
normalizer that differs by even a single rule will silently produce
non-merging duplicates.

## `normalize_disease_name(name: str) -> str`

Deterministic string normalization for disease names. Used by the
`Disease` resolver and by every disease loader in `database/ingestion/`.

### Rules

Applied in order:

1. **Empty-input short-circuit.** If `name` is falsy (`""`, `None` if
   you bypass typing, `0`), returns `""`. Distinguish from "a real
   string that normalizes to empty" — that's impossible with the
   current rules.
2. **Strip + lowercase.** `name.strip().lower()`.
3. **Collapse internal whitespace.** Consecutive whitespace becomes a
   single space (`re.sub(r"\s+", " ", ...)`).
4. **Abbreviation expansion.** If the trimmed-lowercased string matches
   a known abbreviation, replace it with the long form. See
   [the abbreviation table](#abbreviation-table) below.
5. **Strip apostrophes.** Both U+0027 `'` and U+2019 `'` are removed
   (not replaced with a space). `crohn's` → `crohns`.
6. **Replace hyphens with spaces.** `non-alcoholic` → `non alcoholic`.
7. **Re-collapse whitespace + strip.** Any new runs of spaces created
   by step 5 or 6 are collapsed; leading/trailing whitespace removed.

### Abbreviation table

| Input (lowercased + stripped) | Expands to |
|---|---|
| `mdd` | major depressive disorder |
| `ibd` | inflammatory bowel disease |
| `ibs` | irritable bowel syndrome |
| `t2d` | type 2 diabetes |
| `t2dm` | type 2 diabetes |
| `crc` | colorectal cancer |
| `nafld` | non-alcoholic fatty liver disease |
| `ad` | alzheimer's disease |
| `pd` | parkinson's disease |
| `ms` | multiple sclerosis |
| `ra` | rheumatoid arthritis |
| `asd` | autism spectrum disorder |
| `uc` | ulcerative colitis |
| `cd` | crohn's disease |

Abbreviation expansion runs **before** apostrophe removal, so `cd` →
`crohn's disease` → `crohns disease` (post-step-5). The same normalized
form happens whether the input is `cd`, `CD`, `Crohn's Disease`, or
`crohn's disease`.

### Worked examples

| Input | Output | Reason |
|---|---|---|
| `Crohn's Disease` | `crohns disease` | lowercase + strip apostrophe |
| `CD` | `crohns disease` | abbreviation → strip apostrophe |
| `ulcerative colitis` | `ulcerative colitis` | already normal form |
| `UC` | `ulcerative colitis` | abbreviation expansion |
| `Non-Alcoholic Fatty Liver Disease` | `non alcoholic fatty liver disease` | lowercase + hyphen → space |
| `NAFLD` | `non alcoholic fatty liver disease` | abbreviation expansion + hyphen rule |
| `  Type   2   Diabetes  ` | `type 2 diabetes` | strip + collapse whitespace |
| `Parkinson's Disease` | `parkinsons disease` | lowercase + strip apostrophe |
| `PD` | `parkinsons disease` | abbreviation + strip apostrophe |
| `(empty string)` | `(empty string)` | short-circuit |

### Adding a new abbreviation

If your source uses a disease abbreviation not in the table, you have
two options:

1. **Expand the source data before passing to MapForge.** Run a sed/awk
   preprocess that does the substitution.
2. **Add the abbreviation to `DISEASE_ABBREVIATIONS`.** The dict is
   intentionally small and curated — every addition should map to a
   long form that's already present in the loaded MicroMap graph
   (otherwise the resolver won't find anything to merge against). Open
   a PR; the loaders that consume this list will pick up the change.

The function is **deterministic and pure** — no I/O, no global state,
no time-of-day dependency. Same input always produces the same output.

---

## `generate_disease_id(name: str, identifiers: Optional[dict] = None) -> str`

Generates a stable disease ID, preferring standard identifiers over a
name-derived fallback. Used by some loaders to stamp a `disease_id`
property on `:Disease` nodes — but **note that the loaders ultimately
MERGE on `name_normalized`, not `disease_id`** (see
[`ontology.md#disease`](ontology.md#disease)). So this function's output
is informational, not the resolver's lookup key.

### Priority order

The first non-empty value in this order wins. The returned ID is
prefixed by the source namespace.

| `identifiers` key | Output format | Example |
|---|---|---|
| `doid` | `DOID:<value>` | `DOID:8778` |
| `mesh_id` | `MESH:<value>` | `MESH:D003424` |
| `omim_id` | `OMIM:<value>` | `OMIM:266600` |
| `umls_cui` | `UMLS:<value>` | `UMLS:C0010346` |
| `icd10` | `ICD10:<value>` | `ICD10:K50` |

If none of the above are present (or `identifiers` is `None`), falls
back to a name-derived ID built from `normalize_disease_name(name)`
with spaces replaced by underscores:

| Input | Output |
|---|---|
| `name="Crohn's Disease", identifiers={"doid": "8778"}` | `DOID:8778` |
| `name="Crohn's Disease", identifiers={"mesh_id": "D003424"}` | `MESH:D003424` |
| `name="Crohn's Disease", identifiers={"doid": "8778", "mesh_id": "D003424"}` | `DOID:8778` (DOID wins) |
| `name="Crohn's Disease", identifiers={}` | `disease:crohns_disease` |
| `name="Crohn's Disease", identifiers=None` | `disease:crohns_disease` |
| `name="UC"` | `disease:ulcerative_colitis` (abbreviation expansion still applies) |

The name-based fallback prefix is **lowercase `disease:`**, distinct
from the upper-case ontology prefixes for identifier-derived IDs.
That's intentional — it signals "this id has no upstream identifier
backing it and may be fragile across name-variation contributions."

---

## Re-using these helpers in your own code

`micromap_mapforge.normalize` is a public module of the `micromap-mapforge`
package. Import and use directly:

```python
from micromap_mapforge.normalize import normalize_disease_name, generate_disease_id

# Pre-process a CSV before submitting:
import csv
with open("source.csv") as f:
    reader = csv.DictReader(f)
    rows = list(reader)
for row in rows:
    row["name_normalized"] = normalize_disease_name(row["disease"])
    row["disease_id"] = generate_disease_id(row["disease"], identifiers={
        "doid": row.get("doid"),
        "mesh_id": row.get("mesh_id"),
    })
```

The functions are pure and side-effect-free, so they're safe in
unit tests against arbitrary input.

---

## Drift guarantee

`micromap_mapforge/normalize.py` was ported from
`graphomics-kg/database/ingestion/base_loader.py` to keep MapForge a
standalone package. The two implementations need to stay in sync — if a
MicroMap loader bumps the abbreviation table, the MapForge helper has
to bump too. This is currently a manual sync; auto-validation against
the loader is tracked under [#74](https://github.com/vkhangraphomics/MicroMap/issues/74).

---

## Source-of-truth files

| What | Where |
|---|---|
| MapForge helper (this doc's subject) | [`micromap_mapforge/normalize.py`](../../micromap_mapforge/normalize.py) |
| MicroMap loader-side helper (must stay in sync) | `graphomics-kg/database/ingestion/base_loader.py` |
| Resolver that applies `normalize_disease_name`: | [`micromap_mapforge/resolve/disease.py`](../../micromap_mapforge/resolve/disease.py) |
| Mapping that references it as `normalizer`: | [`examples/disbiome/mapping.yaml`](../../../examples/disbiome/mapping.yaml) |

## Cross-references

- Ontology entry for `Disease`: [`ontology.md#disease`](ontology.md#disease)
- Mapping field that opts a Disease entity into normalization: [`mapping-yaml.md#strict-mode-sourceformat--schema_adapter`](mapping-yaml.md#strict-mode-sourceformat--schema_adapter) (the `normalizer` field)
