"""#286 G8a: raw reads/spectra get an actionable error, not a bare extension list.

FASTQ/FASTA/BAM/CRAM/SAM/mzML/vendor-RAW have no column/matrix structure or
KG-mappable entities until a pipeline processes them (Workbench's job). inspect()
recognizes them and points the caller at the pipeline that yields a mappable
artifact, instead of the generic "unsupported file extension".
"""
from pathlib import Path

import gzip
import pytest

from micromap_mapforge.inspect.dispatch import inspect


@pytest.mark.parametrize("name", [
    "reads.fastq", "reads.fq", "genome.fasta", "genome.fa",
    "aln.bam", "aln.cram", "aln.sam", "spectra.mzML", "spectra.mzXML",
])
def test_raw_formats_get_actionable_error(tmp_path: Path, name: str):
    f = tmp_path / name
    f.write_bytes(b"not really a real file")
    with pytest.raises(ValueError, match="(?i)pipeline|workbench|process") as ei:
        inspect(f)
    # actionable, NOT the generic supported-extension dump
    assert "unsupported file extension" not in str(ei.value)


def test_fastq_gz_routes_through_decompress_to_the_raw_error(tmp_path: Path):
    # G0 decompresses .fastq.gz -> inner .fastq -> G8a's actionable error,
    # NOT "unsupported '.gz'".
    f = tmp_path / "reads.fastq.gz"
    f.write_bytes(gzip.compress(b"@r1\nACGT\n+\n!!!!\n"))
    with pytest.raises(ValueError, match="(?i)pipeline|workbench") as ei:
        inspect(f)
    assert ".gz" not in str(ei.value) or "pipeline" in str(ei.value).lower()


def test_truly_unknown_extension_still_gets_the_generic_error(tmp_path: Path):
    f = tmp_path / "mystery.xyz"
    f.write_text("whatever")
    with pytest.raises(ValueError, match="unsupported file extension"):
        inspect(f)
