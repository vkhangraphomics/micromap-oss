"""#286 G0: transparent single-file gzip/bz2/xz decompression.

Only tar-gzip was recognized; a bare `.csv.gz` (how GEO/MGnify/ENA/HMP ship) fell
through to the unsupported-extension raise. inspect() now decompresses a
single-file .gz/.bz2/.xz and re-dispatches on the inner suffix.
"""
import bz2
import gzip
import lzma
from pathlib import Path

import pytest

from micromap_mapforge.inspect.dispatch import inspect

_CSV = b"taxon,disease\nBacteroides,IBD\nPrevotella,healthy\n"
_TSV = b"gene\tcount\nBRCA1\t12\nTP53\t7\n"
_JSON = b'[{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]'


def test_single_file_gzip_csv(tmp_path: Path):
    f = tmp_path / "study.csv.gz"
    f.write_bytes(gzip.compress(_CSV))
    prof = inspect(f)
    assert prof.format == "csv"
    assert [c.name for c in prof.columns] == ["taxon", "disease"]
    assert prof.row_count_estimate == 2
    assert str(f) in prof.path          # path reflects the original, not the temp file
    assert "gz" in prof.note.lower()    # decompression is recorded


def test_single_file_bz2_tsv(tmp_path: Path):
    f = tmp_path / "expr.tsv.bz2"
    f.write_bytes(bz2.compress(_TSV))
    prof = inspect(f)
    assert prof.format == "tsv"
    assert [c.name for c in prof.columns] == ["gene", "count"]


def test_single_file_xz_json(tmp_path: Path):
    f = tmp_path / "s.json.xz"
    f.write_bytes(lzma.compress(_JSON))
    assert inspect(f).format == "json"


def test_gzip_profile_matches_uncompressed(tmp_path: Path):
    raw = tmp_path / "s.csv"
    raw.write_bytes(_CSV)
    gz = tmp_path / "s.csv.gz"
    gz.write_bytes(gzip.compress(_CSV))
    a, b = inspect(raw), inspect(gz)
    assert [c.name for c in a.columns] == [c.name for c in b.columns]
    assert a.row_count_estimate == b.row_count_estimate


def test_compressed_without_inner_extension_errors(tmp_path: Path):
    f = tmp_path / "mystery.gz"    # no inner suffix to profile
    f.write_bytes(gzip.compress(b"whatever"))
    with pytest.raises(ValueError, match="(?i)inner"):
        inspect(f)


def test_tar_gz_is_still_treated_as_an_archive(tmp_path: Path):
    # G0 must NOT hijack tar.* — those stay on the archive path. A .tar.gz with no
    # members raises the archive error, not the single-file decompress path.
    import io
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz"):
        pass
    f = tmp_path / "bundle.tar.gz"
    f.write_bytes(buf.getvalue())
    with pytest.raises(ValueError) as ei:
        inspect(f)
    assert "inner" not in str(ei.value).lower()  # archive path, not the G0 path
