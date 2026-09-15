"""INGEST_REPORT.md writer — the one-page human audit of a bundle."""

from collections import Counter

from ..confidence import Confidence
from ..resolve.pipeline import ResolutionReport
from .bundle import IngestBundle


def write_report(bundle: IngestBundle, report: ResolutionReport) -> None:
    path = bundle.root / "INGEST_REPORT.md"
    path.write_text(_render(report), encoding="utf-8")


def _render(report: ResolutionReport) -> str:
    breakdown: Counter[str] = Counter()
    for row in report.resolved:
        best = row.best
        if best is not None:
            breakdown[best.match_type.value] += 1

    lines = [
        "# Ingest Report",
        "",
        "## Resolution Summary",
        "",
        f"- **Resolved:** {report.resolved_count}",
        f"- **Unresolved:** {report.unresolved_count}",
        f"- **Ambiguous:** {report.ambiguous_count}",
        "",
        "## Confidence Breakdown (resolved entities)",
        "",
    ]
    for label in (Confidence.EXTRACTED.value, Confidence.INFERRED.value):
        lines.append(f"- **{label}:** {breakdown.get(label, 0)}")
    lines.append("")
    lines.append("See `unresolved.md` for unresolved / ambiguous items.")
    return "\n".join(lines) + "\n"
