"""Write resolution.json and unresolved.md from a ResolutionReport."""

import json
from collections import defaultdict
from pathlib import Path

from .pipeline import ResolutionReport


def write_artifacts(report: ResolutionReport, out_dir: "str | Path") -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "resolution.json").write_text(
        json.dumps(report.to_dict(), indent=2), encoding="utf-8"
    )
    (out / "unresolved.md").write_text(_render_md(report), encoding="utf-8")


def _render_md(report: ResolutionReport) -> str:
    lines = [
        "# Unresolved & Ambiguous Entities",
        "",
        f"- **Resolved:** {report.resolved_count}",
        f"- **Unresolved:** {report.unresolved_count}",
        f"- **Ambiguous:** {report.ambiguous_count}",
        "",
    ]
    if report.unresolved_count == 0 and report.ambiguous_count == 0:
        lines.append("_Nothing unresolved. All source entities mapped cleanly._")
        return "\n".join(lines) + "\n"

    if report.unresolved:
        by_label: dict[str, list] = defaultdict(list)
        for row in report.unresolved:
            by_label[row.entity_label].append(row)
        for label in sorted(by_label):
            lines.append(f"## Unresolved — {label}")
            lines.append("")
            for row in by_label[label]:
                lines.append(f"- `{row.source_term}` — no candidates found")
            lines.append("")

    if report.ambiguous:
        by_label2: dict[str, list] = defaultdict(list)
        for row in report.ambiguous:
            by_label2[row.entity_label].append(row)
        for label in sorted(by_label2):
            lines.append(f"## Ambiguous — {label}")
            lines.append("")
            for row in by_label2[label]:
                lines.append(f"- `{row.source_term}`:")
                for c in row.candidates:
                    lines.append(f"    - `{c.node_id}` (score={c.score:.2f}, {c.reason})")
            lines.append("")

    return "\n".join(lines) + "\n"
