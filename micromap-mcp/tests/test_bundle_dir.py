import json
import os
import time
from pathlib import Path
import pytest
from micromap_mcp.bundle_dir import BundleDirManager


def test_create_session_records_owner(tmp_path):
    m = BundleDirManager(base=str(tmp_path), gc_age_days=7)
    p = m.create_session(owner_org="org-a", owner_user="alice")
    data = json.loads((Path(p) / ".owner.json").read_text(encoding="utf-8"))
    assert data["org"] == "org-a"
    assert data["user"] == "alice"
    assert m.owner_org(Path(p)) == "org-a"


def test_owner_org_is_none_when_unrecorded(tmp_path):
    m = BundleDirManager(base=str(tmp_path), gc_age_days=7)
    p = m.create_session()
    assert m.owner_org(Path(p)) is None


def test_create_session_returns_unique_directories(tmp_path):
    m = BundleDirManager(base=str(tmp_path), gc_age_days=7)
    a = m.create_session()
    b = m.create_session()
    assert a != b
    assert Path(a).is_dir()
    assert Path(b).is_dir()


def test_gc_removes_old_dirs_only(tmp_path):
    base = tmp_path
    m = BundleDirManager(base=str(base), gc_age_days=1)
    fresh = m.create_session()
    old = m.create_session()
    # Backdate `old` to 3 days ago
    three_days = time.time() - 3 * 86400
    os.utime(old, (three_days, three_days))
    removed = m.gc()
    assert old in removed
    assert fresh not in removed
    assert not Path(old).exists()
    assert Path(fresh).exists()


def test_resolve_rejects_path_escape(tmp_path):
    m = BundleDirManager(base=str(tmp_path), gc_age_days=7)
    with pytest.raises(ValueError):
        m.resolve("../etc/passwd")


def test_resolve_rejects_base_itself(tmp_path):
    """resolve('') / '.' must NOT return the base directory."""
    m = BundleDirManager(base=str(tmp_path), gc_age_days=7)
    with pytest.raises(ValueError):
        m.resolve("")
    with pytest.raises(ValueError):
        m.resolve(".")


# ---------------------------------------------------------------------------
# #316: contain() — file-level containment (no bundle-dir-only restriction)
# ---------------------------------------------------------------------------


def test_contain_accepts_absolute_path_inside_base(tmp_path):
    m = BundleDirManager(base=str(tmp_path), gc_age_days=7)
    session = m.create_session()
    inside = Path(session) / "data.tsv"
    assert m.contain(str(inside)) == inside.resolve()


def test_contain_resolves_relative_against_base(tmp_path):
    m = BundleDirManager(base=str(tmp_path), gc_age_days=7)
    assert m.contain("abc/data.tsv") == (tmp_path / "abc" / "data.tsv").resolve()


def test_contain_rejects_absolute_path_outside_base(tmp_path):
    m = BundleDirManager(base=str(tmp_path), gc_age_days=7)
    outside = tmp_path.parent / "elsewhere" / "data.tsv"
    with pytest.raises(ValueError):
        m.contain(str(outside))


def test_contain_rejects_traversal(tmp_path):
    m = BundleDirManager(base=str(tmp_path), gc_age_days=7)
    with pytest.raises(ValueError):
        m.contain("../data.tsv")
    with pytest.raises(ValueError):
        m.contain(str(tmp_path / ".." / "data.tsv"))


def test_gc_handles_read_only_files(tmp_path):
    """GC must successfully remove sessions even when they contain read-only files."""
    import stat
    m = BundleDirManager(base=str(tmp_path), gc_age_days=1)
    session = m.create_session()
    artifact = Path(session) / "readonly.cypher"
    artifact.write_text("MERGE (n:Test);")
    # Make the file read-only (Windows would otherwise refuse the unlink)
    os.chmod(artifact, stat.S_IREAD)
    three_days = time.time() - 3 * 86400
    os.utime(session, (three_days, three_days))
    removed = m.gc()
    assert session in removed
    assert not Path(session).exists()
