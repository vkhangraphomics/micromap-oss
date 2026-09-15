import io
import tarfile
from pathlib import Path

import pytest

from api.federation_intake import BundleTooLargeError, UnsafeBundleError, safe_extract_bundle


def _tar_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_extracts_normal_bundle(tmp_path: Path):
    data = _tar_bytes({"routing.yaml": b"destination: micromap-core\n",
                       "cypher/nodes_Taxon.cypher": b"MERGE (n:Taxon);\n"})
    safe_extract_bundle(data, tmp_path)
    assert (tmp_path / "routing.yaml").is_file()
    assert (tmp_path / "cypher" / "nodes_Taxon.cypher").is_file()


def test_rejects_path_traversal(tmp_path: Path):
    data = _tar_bytes({"../evil.txt": b"pwned"})
    with pytest.raises(UnsafeBundleError):
        safe_extract_bundle(data, tmp_path)


def test_rejects_absolute_path(tmp_path: Path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="/etc/passwd")
        info.size = 3
        tar.addfile(info, io.BytesIO(b"abc"))
    with pytest.raises(UnsafeBundleError):
        safe_extract_bundle(buf.getvalue(), tmp_path)


def test_rejects_oversize_total(tmp_path: Path):
    data = _tar_bytes({"big.bin": b"x" * 2048})
    with pytest.raises(BundleTooLargeError):
        safe_extract_bundle(data, tmp_path, max_total_bytes=1024)


def test_rejects_too_many_members(tmp_path: Path):
    data = _tar_bytes({f"f{i}.txt": b"x" for i in range(10)})
    with pytest.raises(BundleTooLargeError):
        safe_extract_bundle(data, tmp_path, max_members=5)


def test_rejects_non_gzip_garbage(tmp_path: Path):
    with pytest.raises(UnsafeBundleError):
        safe_extract_bundle(b"this is not a tar", tmp_path)
