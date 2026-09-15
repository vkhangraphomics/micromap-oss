"""Determinism contract for the inspect → emit pipeline (issue #62 scope).

The existing golden tests (test_m2_golden, test_serialize_golden, …) catch
drift from a fixed reference. This explicit determinism check is the
companion: running the SAME pipeline twice on the same input must produce
byte-identical Cypher and params files. Without that property, the
"re-run is safe" promises that submit/approve depend on can't hold.

LLM-assisted (mode=llm) `map` is intentionally NOT covered here — non-zero
temperature makes LLM responses non-deterministic by design; that
determinism is the LLM provider's contract, not MapForge's.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from click.testing import CliRunner

from micromap_mapforge.cli import main
from micromap_mapforge.confidence import Confidence
from micromap_mapforge.resolve.base import Candidate


def _fake_resolvers():
    """Deterministic resolver stand-ins matching the m2 golden test's pattern."""
    taxon = MagicMock()
    taxon.label = "Taxon"
    taxon.resolve = MagicMock(side_effect=lambda term, ctx: (
        [Candidate(f"ncbi_tax_id:{term}", Confidence.EXTRACTED, 1.0, "", {},
                   "ncbi_tax_id", term)]
        if term.isdigit() else []
    ))
    empty = MagicMock(resolve=MagicMock(return_value=[]))
    return {
        "Taxon": taxon, "Disease": empty, "Metabolite": empty, "Drug": empty,
        "Gene": empty, "Protein": empty, "Pathway": empty, "Paper": empty,
        "BodySite": empty,
    }


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _run_pipeline(out_dir: Path) -> dict[str, str]:
    """Run inspect → map → resolve → plan → emit and return a dict of
    bundle-relative-path → file contents."""
    runner = CliRunner()

    r = runner.invoke(main, ["map", str(FIXTURES / "study.tsv"),
                             "--out", str(out_dir), "--mode", "heuristic"])
    assert r.exit_code == 0, r.output

    with patch("micromap_mapforge.cli._build_resolvers",
               return_value=_fake_resolvers()):
        r = runner.invoke(main, ["resolve", "--bundle", str(out_dir),
                                 "--neo4j-uri", "bolt://localhost:7687",
                                 "--neo4j-user", "neo4j",
                                 "--neo4j-password", "x"])
        assert r.exit_code == 0, r.output

    r = runner.invoke(main, ["plan", "--bundle", str(out_dir),
                             "--organization-id", "det-org"])
    assert r.exit_code == 0, r.output

    r = runner.invoke(main, ["emit", "--bundle", str(out_dir)])
    assert r.exit_code == 0, r.output

    # Collect deterministic outputs. Skip the inspection report (Markdown
    # rendering of float samples can include tiny formatting variations
    # and isn't part of the contract this test pins) and skip manifest.json
    # (its file-list hash IS deterministic for the same content, but the
    # manifest also carries approval state that's flipped separately).
    deterministic_files = []
    cypher_dir = out_dir / "cypher"
    if cypher_dir.exists():
        for p in sorted(cypher_dir.iterdir()):
            deterministic_files.append(p)
    for name in ("mapping.yaml", "routing.yaml", "resolution.json"):
        candidate = out_dir / name
        if candidate.exists():
            deterministic_files.append(candidate)

    snapshot: dict[str, str] = {}
    for p in deterministic_files:
        snapshot[p.relative_to(out_dir).as_posix()] = p.read_text(encoding="utf-8")
    return snapshot


def test_pipeline_is_deterministic_across_two_runs(tmp_path):
    """Same input + same fake resolvers + same routing flags → byte-identical
    Cypher, params, mapping, routing, resolution outputs."""
    snap_a = _run_pipeline(tmp_path / "run-a")
    snap_b = _run_pipeline(tmp_path / "run-b")

    # Same set of files.
    assert set(snap_a) == set(snap_b), (
        f"file sets differ between runs:\n  only in A: "
        f"{sorted(set(snap_a) - set(snap_b))}\n  only in B: "
        f"{sorted(set(snap_b) - set(snap_a))}"
    )

    # Same byte content per file.
    drift: list[str] = []
    for name in sorted(snap_a):
        # Strip per-run absolute paths inside mapping.yaml's source.path.
        # The pipeline'\''s "deterministic for same input" contract is about
        # logical equivalence; absolute paths legitimately differ per tmp_path.
        if name == "mapping.yaml":
            a_mapping = yaml.safe_load(snap_a[name])
            b_mapping = yaml.safe_load(snap_b[name])
            a_mapping["source"].pop("path", None)
            b_mapping["source"].pop("path", None)
            if a_mapping != b_mapping:
                drift.append(name)
            continue
        # routing.yaml has a `submitted_at` timestamp that's non-deterministic
        # by design (it records the actual plan-step time).
        if name == "routing.yaml":
            a_routing = yaml.safe_load(snap_a[name])
            b_routing = yaml.safe_load(snap_b[name])
            for d in (a_routing, b_routing):
                d.get("provenance", {}).pop("submitted_at", None)
            if a_routing != b_routing:
                drift.append(name)
            continue
        if snap_a[name] != snap_b[name]:
            drift.append(name)

    assert not drift, (
        "These files drifted between two identical runs of the pipeline. "
        "Deterministic re-runs are a load-bearing assumption for submit/"
        "approve idempotency (issue #62):\n  " + "\n  ".join(drift)
    )
