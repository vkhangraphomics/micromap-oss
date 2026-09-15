import json
from pathlib import Path

from micromap_mapforge.confidence import Confidence
from micromap_mapforge.resolve.artifacts import write_artifacts
from micromap_mapforge.resolve.base import Candidate, ResolutionRow
from micromap_mapforge.resolve.pipeline import ResolutionReport


def _make_report():
    return ResolutionReport(
        resolved=[
            ResolutionRow("Taxon", "562",
                          [Candidate("NCBI:562", Confidence.EXTRACTED, 1.0, "exact", {})]),
            ResolutionRow("Disease", "CD",
                          [Candidate("DOID:8778", Confidence.INFERRED, 0.95, "normalized-name", {})]),
        ],
        unresolved=[
            ResolutionRow("Taxon", "9999", []),
        ],
        ambiguous=[
            ResolutionRow("Disease", "cancer",
                          [Candidate("DOID:162", Confidence.INFERRED, 0.6, "fuzzy", {}),
                           Candidate("DOID:14566", Confidence.INFERRED, 0.6, "fuzzy", {})]),
        ],
    )


def test_write_artifacts_creates_json(tmp_path: Path):
    report = _make_report()
    write_artifacts(report, tmp_path)

    json_path = tmp_path / "resolution.json"
    assert json_path.exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["resolved_count"] == 2
    assert data["unresolved_count"] == 1
    assert data["ambiguous_count"] == 1


def test_write_artifacts_creates_unresolved_md(tmp_path: Path):
    report = _make_report()
    write_artifacts(report, tmp_path)

    md_path = tmp_path / "unresolved.md"
    assert md_path.exists()
    md = md_path.read_text(encoding="utf-8")
    # Unresolved items grouped by entity label
    assert "## Unresolved — Taxon" in md
    assert "9999" in md
    # Ambiguous section with candidate listing
    assert "## Ambiguous — Disease" in md
    assert "cancer" in md
    assert "DOID:162" in md
    assert "DOID:14566" in md


def test_write_artifacts_handles_empty_report(tmp_path: Path):
    write_artifacts(ResolutionReport(), tmp_path)
    assert (tmp_path / "resolution.json").exists()
    assert (tmp_path / "unresolved.md").exists()
    md = (tmp_path / "unresolved.md").read_text(encoding="utf-8")
    assert "nothing unresolved" in md.lower() or "no unresolved" in md.lower()
