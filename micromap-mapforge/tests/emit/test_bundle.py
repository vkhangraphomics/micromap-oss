import json
from pathlib import Path

from micromap_mapforge.confidence import Confidence
from micromap_mapforge.emit.bundle import (
    build_bundle,
    load_bundle,
    write_manifest,
)
from micromap_mapforge.emit.report import write_report
from micromap_mapforge.resolve.base import Candidate, ResolutionRow
from micromap_mapforge.resolve.pipeline import ResolutionReport


def _seed(tmp_path: Path) -> Path:
    out = tmp_path / "out"
    (out / "cypher").mkdir(parents=True)
    (out / "cypher" / "nodes_Taxon.cypher").write_text("MERGE (n:Taxon {});", encoding="utf-8")
    (out / "mapping.yaml").write_text("source: {name: s, format: tsv, path: s.tsv}\nentities: []\nrelationships: []\n", encoding="utf-8")
    (out / "routing.yaml").write_text("destination: micromap-core\n", encoding="utf-8")
    (out / "resolution.json").write_text("{}", encoding="utf-8")
    (out / "inspection.json").write_text("{}", encoding="utf-8")
    return out


def test_build_bundle_from_directory(tmp_path: Path):
    out = _seed(tmp_path)
    bundle = build_bundle(out)
    assert bundle.root == out
    assert bundle.mapping_path == out / "mapping.yaml"
    assert bundle.routing_path == out / "routing.yaml"


def test_write_manifest_records_checksums(tmp_path: Path):
    out = _seed(tmp_path)
    bundle = build_bundle(out)
    write_manifest(bundle)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert "mapping.yaml" in manifest["files"]
    entry = manifest["files"]["mapping.yaml"]
    assert entry["sha256"].startswith(("a", "b", "c", "d", "e", "f", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"))
    assert entry["size"] > 0


def test_load_bundle_reads_manifest(tmp_path: Path):
    out = _seed(tmp_path)
    bundle = build_bundle(out)
    write_manifest(bundle)
    loaded = load_bundle(out)
    assert loaded.manifest is not None
    assert "mapping.yaml" in loaded.manifest["files"]


def test_write_report_produces_markdown(tmp_path: Path):
    out = _seed(tmp_path)
    bundle = build_bundle(out)
    report = ResolutionReport(
        resolved=[
            ResolutionRow("Taxon", "562", [Candidate("NCBI:562", Confidence.EXTRACTED, 1.0, "", {})]),
        ],
        unresolved=[ResolutionRow("Disease", "X", [])],
    )
    write_report(bundle, report)
    md = (out / "INGEST_REPORT.md").read_text(encoding="utf-8")
    assert "Resolved" in md
    assert "Unresolved" in md
    # Confidence breakdown visible
    assert "EXTRACTED" in md


def test_sha256_matches_naive_full_read_for_large_file(tmp_path):
    import hashlib
    from micromap_mapforge.emit.bundle import _sha256

    content = (b"abcdefgh" * 1024) * 500  # 4,096,000 bytes
    p = tmp_path / "big.bin"
    p.write_bytes(content)

    expected = hashlib.sha256(content).hexdigest()
    assert _sha256(p) == expected
