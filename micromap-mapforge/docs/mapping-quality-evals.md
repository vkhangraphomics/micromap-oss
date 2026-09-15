# Mapping-quality evals — heuristic vs LLM, with numbers

> Answers the product question from [#266](https://github.com/vkhangraphomics/MicroMap/issues/266):
> **when should a contributor use heuristic mapping vs LLM mapping — and why?**
> Not with a hunch, but by scoring both arms against curated gold mappings.

Stage 2 of the contributor flow ([`contributor-flow.md`](contributor-flow.md#stage-2--map))
drafts a `mapping.yaml` two ways: the offline **heuristic** (`mapforge map`) and the
Anthropic-assisted **LLM** (`mapforge map --mode llm`). This suite measures how good
each draft is against a hand-authored gold mapping, on three axes a contributor
actually cares about.

## What's measured

The scorer ([`micromap_mapforge/evals/mapping_quality.py`](../micromap_mapforge/evals/mapping_quality.py))
is pure and deterministic — the same measuring stick for both arms:

| axis | question | how |
|---|---|---|
| **entity F1** | did we recover the right node labels? | precision/recall/F1 over `entity.label` |
| **match_on** | did we pick the right key column per entity? | fraction of shared labels whose `match_on` equals gold's |
| **column F1** | did we map the right source column to the right ontology field? | P/R/F1 over `(label, field, source_col)` triples |
| **rel recall** | did we recover the graph edges? | recall over `(type, from-label, to-label)` signatures |

Ground truth is the five shipped gold bundles under [`examples/`](../../examples) —
one per discipline, each a source CSV plus a curated gold `mapping.yaml` (the
"gold path" for production). No synthetic fixtures: the examples *are* the gold.

## Results

Run on the five gold bundles; LLM arm = `claude-sonnet-4-6`, mean of 2 runs per case.

| case | regime | gold rels | arm | entity F1 | match_on | column F1 | rel recall | rels emitted | cost |
|---|---|---|---|---|---|---|---|---|---|
| **microbiome / disbiome** | conventional | 2 | heuristic | **1.00** | 0.67 | 0.62 | 0.00 | 0 | ~1 ms, offline, deterministic |
| | | | llm | 1.00 | **1.00** | **0.86** | **1.00** | 3 | ~7.4 s, stable |
| **metabolomics / serum** | conventional | 3 | heuristic | **1.00** | 0.25 | 0.43 | 0.00 | 0 | ~1 ms, offline, deterministic |
| | | | llm | 1.00 | **0.75** | **0.80** | **1.00** | 3 | ~7.2 s, stable |
| **proteomics / plasma** | conventional | 4 | heuristic | **1.00** | 0.20 | 0.59 | 0.00 | 0 | ~1 ms, offline, deterministic |
| | | | llm | 1.00 | 0.20 | **0.78** | **0.75** | 3 | ~7.2 s, stable |
| **transcriptomics / alzheimer** | mixed | 3 | heuristic | **1.00** | **0.25** | 0.71 | 0.00 | 0 | ~1 ms, offline, deterministic |
| | | | llm | 1.00 | 0.00 | 0.71 | **1.00** | 4 | ~7.9 s, stable |
| **genomics / brca** | mixed | 3 | heuristic | **1.00** | 0.20 | 0.63 | 0.00 | 0 | ~1 ms, offline, deterministic |
| | | | llm | 1.00 | **0.60** | **0.78** | **1.00** | 4 | ~8.2 s, stable |

Bold marks the better (or tied-best) arm per cell.

## What the numbers say

1. **Entity recovery is a tie — always 1.00, both arms.** The heuristic recovers every
   gold entity label as well as the LLM, on all five disciplines, at ~1 ms and zero cost.
   For *entity scaffolding, the heuristic is not worse* — it is the rational default.
2. **Relationships are the LLM's decisive win.** The heuristic emits `relationships: []`
   every time (recall **0.00** on every case, by construction). The LLM recovers most or
   all gold edges (recall **0.75–1.00**, 3–4 relationships per case). If you need
   relationships, the heuristic cannot give them to you — only the LLM (or hand-authoring) can.
3. **Column & key mapping: the LLM is usually — not always — better.** LLM column F1 beats
   or ties the heuristic on all five; `match_on` is better on three, tied on one, and
   **worse on transcriptomics** (0.00 vs 0.25). The LLM is a stronger column mapper on
   messy/aliased columns, but it is not a guarantee — review its output.
4. **Cost is asymmetric.** Heuristic: ~1 ms, offline, no key, byte-for-byte deterministic.
   LLM: ~7–8 s, an API key, tokens, and non-determinism (here "stable" across 2 runs, but
   that is observed, not promised — re-running can change columns).
5. **Neither arm is production-grade on its own.** Even the LLM tops out at ~0.86 column F1
   and made a `match_on` error. Both are *drafts*; the hand-authored gold remains the bar.

## Decision: heuristic or LLM?

| use… | when |
|---|---|
| **Heuristic** (default) | You need entity scaffolding, your columns look like conventional identifiers, you'll hand-author relationships anyway, or you need offline / CI / deterministic / no-key / no-cost. It ties the LLM on entities for free. |
| **LLM** (then review) | You need **relationship** proposals — the heuristic gives none — or your columns are messy/aliased and you want a stronger column/key draft, or a natural-language `--hint` can disambiguate the rows. Budget ~8 s + tokens and expect to review. |
| **Hand-authored** (production) | The source will be loaded for real. Treat either arm as a starting draft and finish by hand (`examples/disbiome/mapping.yaml` is the reference). |

**One-line rule:** default to the heuristic for entities; reach for the LLM when you need
relationships or a messy source resists the hints — and review either before you submit.

## Running the suite

```bash
# Heuristic arm — offline, deterministic, CI-safe:
python -m micromap_mapforge.evals.run

# + LLM arm (needs ANTHROPIC_API_KEY):
python -m micromap_mapforge.evals.run --llm
```

The heuristic arm is a **regression guard** in CI
([`tests/evals/test_heuristic_arm.py`](../tests/evals/test_heuristic_arm.py)): a
schema_config hint change that drops entity recovery below 0.9, or a regression that
makes the heuristic start emitting relationships, fails the build. The LLM arm is opt-in
(skips without a key) because it is non-deterministic and costs tokens — run it locally
when you change the prompt or the model, and refresh the table above.

## Caveats

- **`match_on` is scored by field name, strictly.** A gold `match_on: compound_id` and a
  produced `match_on: hmdb_id` that resolve to the *same source column* score as a miss —
  which is why the heuristic's `match_on` looks low even when the resolved column is right.
  The number reflects fidelity to the curated field choice, not resolution failure.
- **Non-determinism.** LLM numbers are the mean of 2 runs; a bigger sample would tighten
  them. `run.py` reports whether column F1 was stable across the runs.
- **Scope.** This measures Stage-2 *mapping* quality only — not resolve/submit accuracy
  (separate) and not performance SLAs ([#79](https://github.com/vkhangraphomics/MicroMap/issues/79)).
