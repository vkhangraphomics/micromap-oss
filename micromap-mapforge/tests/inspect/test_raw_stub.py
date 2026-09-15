"""#286 G8b: opt-in provenance-only stub for raw reads/spectra.

G8a rejects a raw file (.fastq/.bam/.mzML …) with an actionable error by default.
G8b adds an OPT-IN (`inspect(path, as_source=True)`) that instead returns a
lightweight provenance-only profile — format, size, sha256, read count, NO
columns — so a raw file can be *referenced* as a Study/Sample source without
being parsed. The default (as_source=False) still rejects, so G8a is preserved.
"""
import gzip
import hashlib
from pathlib import Path

import pytest

from micromap_mapforge.inspect.dispatch import inspect
from micromap_mapforge.inspect.raw import raw_provenance_profile

_FASTQ = b"@r1\nACGT\n+\n!!!!\n@r2\nTTTT\n+\n####\n@r3\nGGGG\n+\n$$$$\n"  # 3 reads
_FASTA = b">seq1\nACGTACGT\n>seq2\nTTTTGGGG\n"                             # 2 records


def test_as_source_returns_a_provenance_stub_for_fastq(tmp_path: Path):
    f = tmp_path / "reads.fastq"
    f.write_bytes(_FASTQ)
    prof = inspect(f, as_source=True)
    assert prof.format == "fastq"
    assert prof.columns == []                      # not parsed — no column surface
    assert prof.row_count_estimate == 3            # 3 reads (lines // 4)
    assert prof.meta["sha256"] == hashlib.sha256(_FASTQ).hexdigest()
    assert prof.meta["size_bytes"] == len(_FASTQ)
    assert prof.meta["read_count"] == 3


def test_default_still_rejects_raw_without_the_flag(tmp_path: Path):
    # G8a preserved: no as_source -> actionable rejection, not a stub.
    f = tmp_path / "reads.fastq"
    f.write_bytes(_FASTQ)
    with pytest.raises(ValueError, match="(?i)pipeline|workbench"):
        inspect(f)


def test_fasta_read_count_is_record_count(tmp_path: Path):
    f = tmp_path / "genome.fasta"
    f.write_bytes(_FASTA)
    prof = inspect(f, as_source=True)
    assert prof.format == "fasta"
    assert prof.row_count_estimate == 2            # 2 '>' records


def test_binary_raw_has_metadata_but_unknown_read_count(tmp_path: Path):
    # BAM/CRAM/mzML need a real parser to count records; the stub still records
    # size + sha256 and reports read_count as unknown (-1) rather than guessing.
    payload = b"BAM\x01\x00\x00\x00garbage-bytes"
    f = tmp_path / "aln.bam"
    f.write_bytes(payload)
    prof = inspect(f, as_source=True)
    assert prof.format == "bam"
    assert prof.row_count_estimate == -1
    assert prof.meta["sha256"] == hashlib.sha256(payload).hexdigest()
    assert prof.meta["read_count"] is None


def test_gzipped_fastq_stub_hashes_the_real_file_and_counts_reads(tmp_path: Path):
    # Raw reads almost always ship gzipped. The stub must hash the ACTUAL .gz
    # bytes (provenance identity of the file on disk) yet still count reads by
    # decompressing.
    raw = _FASTQ
    gz_bytes = gzip.compress(raw)
    f = tmp_path / "reads.fastq.gz"
    f.write_bytes(gz_bytes)
    prof = inspect(f, as_source=True)
    assert prof.format == "fastq"
    assert prof.meta["sha256"] == hashlib.sha256(gz_bytes).hexdigest()  # the .gz, not inner
    assert prof.meta["size_bytes"] == len(gz_bytes)
    assert prof.row_count_estimate == 3            # counted from decompressed content


def test_as_source_is_a_noop_for_a_normal_tabular_file(tmp_path: Path):
    # The flag only changes behavior for raw formats; a .csv is still parsed.
    f = tmp_path / "study.csv"
    f.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    prof = inspect(f, as_source=True)
    assert prof.format == "csv"
    assert [c.name for c in prof.columns] == ["a", "b"]


def test_raw_provenance_profile_direct(tmp_path: Path):
    f = tmp_path / "reads.fq"
    f.write_bytes(_FASTQ)
    prof = raw_provenance_profile(f)
    assert prof.format == "fastq"                  # .fq normalizes to fastq
    assert "provenance" in prof.note.lower() or "not parsed" in prof.note.lower()


def test_cli_inspect_as_source_writes_provenance_report(tmp_path: Path):
    import json

    from click.testing import CliRunner

    from micromap_mapforge.cli import main

    f = tmp_path / "reads.fastq"
    f.write_bytes(_FASTQ)
    out = tmp_path / "out"
    res = CliRunner().invoke(main, ["inspect", str(f), "--as-source", "--out", str(out)])
    assert res.exit_code == 0, res.output
    report = json.loads((out / "inspection.json").read_text())
    assert report["format"] == "fastq"
    assert report["meta"]["read_count"] == 3
    assert report["meta"]["sha256"] == hashlib.sha256(_FASTQ).hexdigest()
    # And the human report surfaces the provenance identity.
    md = (out / "inspection-report.md").read_text()
    assert "sha256" in md and "Read count" in md


def test_cli_inspect_raw_without_flag_errors_actionably(tmp_path: Path):
    from click.testing import CliRunner

    from micromap_mapforge.cli import main

    f = tmp_path / "reads.fastq"
    f.write_bytes(_FASTQ)
    out = tmp_path / "out"
    res = CliRunner().invoke(main, ["inspect", str(f), "--out", str(out)])
    assert res.exit_code == 2                       # rejected
    assert "pipeline" in res.output.lower() or "workbench" in res.output.lower()
    assert "--as-source" in res.output              # points at the opt-in
