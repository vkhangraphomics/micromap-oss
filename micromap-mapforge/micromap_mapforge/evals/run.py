"""#266: run the mapping-quality eval — score the heuristic and (optionally) LLM
mapping arms against the curated gold bundles, and render a decision table.

- ``run_heuristic`` is offline/deterministic and always runs (CI-safe).
- ``run_llm`` needs ``ANTHROPIC_API_KEY``; it runs each case a few times to
  observe determinism, and is skipped (returns None) without a key.

Run as a script to print the markdown report::

    python -m micromap_mapforge.evals.run           # heuristic only
    python -m micromap_mapforge.evals.run --llm      # + LLM arm (needs a key)
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

import yaml

from ..inspect.dispatch import inspect
from ..mapping.mapper import draft_heuristic_mapping, propose_mapping
from ..mapping.schema_config import load_schema_config
from .cases import EvalCase, default_cases
from .mapping_quality import MappingScore, score_mapping

LLM_MODEL = "claude-sonnet-4-6"
LLM_RUNS = 2  # per case, to observe (non-)determinism without spending much


@dataclass
class ArmResult:
    arm: str                       # "heuristic" | "llm"
    score: MappingScore | None
    latency_s: float | None
    n_relationships: int           # relationships the arm produced
    determinism: str | None = None  # LLM only: "stable" | "varies (...)" | None
    error: str | None = None


def _load_gold(case: EvalCase) -> dict:
    return yaml.safe_load(case.gold_mapping.read_text(encoding="utf-8"))


def _profile_and_config(case: EvalCase):
    return inspect(case.source_csv), load_schema_config(case.discipline)


def run_heuristic(case: EvalCase) -> ArmResult:
    profile, cfg = _profile_and_config(case)
    t = time.time()
    produced = draft_heuristic_mapping(profile, cfg)
    latency = time.time() - t
    return ArmResult(
        arm="heuristic",
        score=score_mapping(produced, _load_gold(case)),
        latency_s=latency,
        n_relationships=len(produced.get("relationships") or []),
    )


def _mean_score(scores: list[MappingScore]) -> MappingScore:
    """Axis-wise mean of several runs' scores (LLM is non-deterministic)."""
    from .mapping_quality import PRF
    n = len(scores)

    def mean_prf(attr) -> PRF:
        ps = [getattr(s, attr) for s in scores]
        return PRF(
            sum(p.precision for p in ps) / n,
            sum(p.recall for p in ps) / n,
            sum(p.f1 for p in ps) / n,
        )

    return MappingScore(
        entities=mean_prf("entities"),
        match_on_accuracy=sum(s.match_on_accuracy for s in scores) / n,
        columns=mean_prf("columns"),
        relationships=mean_prf("relationships"),
    )


def run_llm(case: EvalCase, runs: int = LLM_RUNS, model: str = LLM_MODEL) -> ArmResult:
    profile, cfg = _profile_and_config(case)
    scores: list[MappingScore] = []
    n_rels: list[int] = []
    latencies: list[float] = []
    gold = _load_gold(case)
    try:
        for _ in range(runs):
            t = time.time()
            produced = propose_mapping(profile, hint=case.hint, schema_config=cfg, model=model)
            latencies.append(time.time() - t)
            scores.append(score_mapping(produced, gold))
            n_rels.append(len(produced.get("relationships") or []))
    except Exception as exc:
        return ArmResult(arm="llm", score=None, latency_s=None, n_relationships=0,
                         error=f"{type(exc).__name__}: {exc}")

    f1s = {round(s.columns.f1, 3) for s in scores}
    determinism = "stable" if len(f1s) == 1 else f"varies (col F1 {min(f1s):.2f}-{max(f1s):.2f})"
    return ArmResult(
        arm="llm",
        score=_mean_score(scores),
        latency_s=sum(latencies) / len(latencies),
        n_relationships=round(sum(n_rels) / len(n_rels)),
        determinism=determinism,
    )


@dataclass
class CaseResult:
    case: EvalCase
    gold_relationships: int
    heuristic: ArmResult
    llm: ArmResult | None


def run_case(case: EvalCase, include_llm: bool) -> CaseResult:
    gold = _load_gold(case)
    return CaseResult(
        case=case,
        gold_relationships=len(gold.get("relationships") or []),
        heuristic=run_heuristic(case),
        llm=run_llm(case) if include_llm else None,
    )


def run_all(include_llm: bool, cases: list[EvalCase] | None = None) -> list[CaseResult]:
    return [run_case(c, include_llm) for c in (cases or default_cases())]


def _fmt_arm(a: ArmResult | None) -> str:
    if a is None:
        return "| _(skipped — no ANTHROPIC_API_KEY)_ |||||||"
    if a.error:
        return f"| {a.arm} | error | | | | | {a.error[:40]} |"
    s = a.score
    lat = f"{a.latency_s*1000:.0f} ms" if a.latency_s is not None and a.latency_s < 1 else f"{a.latency_s:.1f} s"
    det = a.determinism or "deterministic"
    return (f"| {a.arm} | {s.entities.f1:.2f} | {s.match_on_accuracy:.2f} | "
            f"{s.columns.f1:.2f} | {s.relationships.recall:.2f} | {a.n_relationships} | "
            f"{lat} · {det} |")


def render_report(results: list[CaseResult]) -> str:
    lines = [
        "| case | regime | gold rels | arm | entity F1 | match_on | column F1 | rel recall | rels | cost |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        head = f"| **{r.case.name}** | {r.case.columns_regime} | {r.gold_relationships} |"
        lines.append(head + _fmt_arm(r.heuristic).split("|", 1)[1])
        if r.llm is not None:
            lines.append("| | | |" + _fmt_arm(r.llm).split("|", 1)[1])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    import sys
    argv = sys.argv[1:] if argv is None else argv
    include_llm = "--llm" in argv
    if include_llm and not os.environ.get("ANTHROPIC_API_KEY"):
        print("--llm given but ANTHROPIC_API_KEY is unset; running heuristic only.")
        include_llm = False
    results = run_all(include_llm=include_llm)
    print(render_report(results))


if __name__ == "__main__":
    main()
