"""#286 G8: raw reads/spectra — the format knowledge + the G8b provenance stub.

FASTQ/FASTA/BAM/CRAM/SAM/mzML/vendor-RAW have no column/matrix structure or
KG-mappable entities until a pipeline processes them (Workbench's job, not
ingest's). By default (G8a) ``dispatch.inspect`` recognizes them and raises an
actionable error naming the next step. G8b adds an OPT-IN
(``inspect(path, as_source=True)``) that instead returns a lightweight
provenance-only ``SourceProfile`` — format, size, sha256, read count, and NO
columns — so a raw file can be *referenced* as a Study/Sample source without
being parsed. Pure/offline.
"""
from __future__ import annotations

import bz2
import gzip
import hashlib
import lzma
from pathlib import Path

from .types import SourceProfile, register_format

#: Raw read/spectra extensions → human description. Owned here (not dispatch) so
#: both the G8a actionable error and the G8b stub read from one source of truth.
RAW_FORMATS: dict[str, str] = {
    ".fastq": "raw sequencing reads", ".fq": "raw sequencing reads",
    ".fasta": "raw sequences", ".fa": "raw sequences", ".fna": "raw sequences",
    ".sam": "aligned reads", ".bam": "aligned reads", ".cram": "aligned reads",
    ".mzml": "raw mass spectra", ".mzxml": "raw mass spectra",
    ".raw": "vendor raw spectra", ".wiff": "vendor raw spectra",
}

# Canonical format token per extension (aliases collapse: .fq→fastq, .fa/.fna→fasta).
_FORMAT_TOKEN = {
    ".fastq": "fastq", ".fq": "fastq",
    ".fasta": "fasta", ".fa": "fasta", ".fna": "fasta",
    ".sam": "sam", ".bam": "bam", ".cram": "cram",
    ".mzml": "mzml", ".mzxml": "mzxml", ".raw": "raw", ".wiff": "wiff",
}
for _tok in set(_FORMAT_TOKEN.values()):
    register_format(_tok)

_COMPRESSION = {".gz": gzip.open, ".bz2": bz2.open, ".xz": lzma.open}
_READ_SHA_CHUNK = 1 << 20  # 1 MiB


def _raw_ext(p: Path) -> str:
    """The effective raw extension, seeing through a single-file .gz/.bz2/.xz
    (``reads.fastq.gz`` → ``.fastq``)."""
    ext = p.suffix.lower()
    if ext in _COMPRESSION:
        ext = Path(p.stem).suffix.lower()
    return ext


def is_raw_source(p: Path) -> bool:
    """True if the path (or its inner suffix under compression) is a raw format."""
    return _raw_ext(p) in RAW_FORMATS


def _file_sha256_and_size(p: Path) -> tuple[str, int]:
    """sha256 + byte size of the file AS IT IS ON DISK (the .gz itself for a
    compressed raw file — that is its provenance identity)."""
    h = hashlib.sha256()
    size = 0
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_READ_SHA_CHUNK), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def _open_text(p: Path):
    """Open a raw text file for reading, transparently decompressing .gz/.bz2/.xz."""
    opener = _COMPRESSION.get(p.suffix.lower(), open)
    return opener(p, "rt", encoding="utf-8", errors="ignore")


def _read_count(p: Path, token: str) -> int | None:
    """Cheap read/record count for text formats; None when a real parser would
    be required (binary BAM/CRAM/mzML/vendor-RAW) — we never guess."""
    if token == "fastq":
        with _open_text(p) as fh:
            lines = sum(1 for _ in fh)
        return lines // 4                        # FASTQ = 4 lines per read
    if token == "fasta":
        with _open_text(p) as fh:
            return sum(1 for ln in fh if ln.startswith(">"))
    if token == "sam":
        with _open_text(p) as fh:
            return sum(1 for ln in fh if ln and not ln.startswith("@"))
    return None                                  # bam/cram/mzml/mzxml/raw/wiff


def raw_provenance_profile(path: str | Path) -> SourceProfile:
    """G8b: a provenance-only SourceProfile for a raw file — no columns, just
    enough identity (format, size, sha256, read count) to reference it as a
    source. sha256/size are of the file on disk; read_count decompresses when
    the file is a gzipped text format."""
    p = Path(path)
    ext = _raw_ext(p)
    kind = RAW_FORMATS.get(ext, "raw data")
    token = _FORMAT_TOKEN.get(ext, ext.lstrip("."))
    sha, size = _file_sha256_and_size(p)
    reads = _read_count(p, token)

    read_note = f"{reads} reads" if reads is not None else "read count not derivable offline"
    note = (
        f"{kind}; provenance-only stub — not parsed (no column/matrix structure "
        f"until a pipeline processes it). size={size} bytes, sha256={sha[:12]}…, "
        f"{read_note}. Run a Workbench pipeline (e.g. taxonomic profiling, "
        f"quantification, or variant calling) to produce a mappable artifact, "
        f"then ingest that."
    )
    return SourceProfile(
        path=str(p),
        format=token,
        row_count_estimate=reads if reads is not None else -1,
        columns=[],                              # deliberately no column surface
        samples=[],
        note=note,
        meta={"kind": kind, "size_bytes": size, "sha256": sha, "read_count": reads},
    )


__all__ = ["RAW_FORMATS", "is_raw_source", "raw_provenance_profile"]
