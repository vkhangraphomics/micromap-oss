"""Archive expansion for the inspector — C4 (#76)."""
import io
import tarfile
import zipfile
from pathlib import Path

import openpyxl
import pytest

from micromap_mapforge.inspect.archive import (
    UnsafeArchiveError,
    archive_members,
    inspect_archive,
)
from micromap_mapforge.inspect.dispatch import inspect

_CSV = b"a,b\n1,2\n3,4\n"


def _zip(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


def _targz(path: Path, files: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:gz") as t:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return path


def test_single_member_zip(tmp_path):
    p = _zip(tmp_path / "one.zip", {"data.csv": _CSV})
    prof = inspect_archive(p)
    assert prof.path.endswith("one.zip::data.csv")
    assert [c.name for c in prof.columns] == ["a", "b"]
    assert prof.row_count_estimate == 2
    assert prof.note == ""  # single member -> no note


def test_multi_member_zip_notes_and_selects(tmp_path):
    p = _zip(tmp_path / "multi.zip", {"taxa.csv": _CSV, "diseases.tsv": b"x\ty\n1\t2\n"})
    prof = inspect_archive(p)
    assert prof.path.endswith("::diseases.tsv")  # sorted: diseases.tsv before taxa.csv
    assert "taxa.csv" in prof.note and "diseases.tsv" in prof.note
    assert archive_members(p) == ["diseases.tsv", "taxa.csv"]
    chosen = inspect_archive(p, member="taxa.csv")
    assert chosen.path.endswith("::taxa.csv") and [c.name for c in chosen.columns] == ["a", "b"]


def test_targz_works(tmp_path):
    p = _targz(tmp_path / "d.tar.gz", {"d.csv": _CSV})
    prof = inspect_archive(p)
    assert prof.path.endswith("d.tar.gz::d.csv") and prof.row_count_estimate == 2


def test_nested_dir_member(tmp_path):
    p = _zip(tmp_path / "nested.zip", {"sub/dir/data.csv": _CSV})
    assert archive_members(p) == ["sub/dir/data.csv"]
    assert inspect_archive(p).row_count_estimate == 2


def test_empty_archive_raises(tmp_path):
    p = _zip(tmp_path / "empty.zip", {"readme.txt": b"hi", "notes.md": b"x"})
    with pytest.raises(ValueError, match="no inspectable sources"):
        inspect_archive(p)


def test_unknown_member_raises(tmp_path):
    p = _zip(tmp_path / "m.zip", {"a.csv": _CSV})
    with pytest.raises(ValueError, match="no inspectable member"):
        inspect_archive(p, member="nope.csv")


def test_zip_slip_rejected(tmp_path):
    p = _zip(tmp_path / "evil.zip", {"../evil.csv": _CSV})
    with pytest.raises(UnsafeArchiveError):
        inspect_archive(p)


def test_xlsx_inside_archive_routes_to_xlsx(tmp_path):
    xl = tmp_path / "book.xlsx"
    wb = openpyxl.Workbook()
    wb.active.append(["tax_id", "name"])
    wb.active.append([562, "E. coli"])
    wb.save(xl)
    p = _zip(tmp_path / "withxlsx.zip", {"book.xlsx": xl.read_bytes()})
    prof = inspect_archive(p)
    assert prof.format == "xlsx" and [c.name for c in prof.columns] == ["tax_id", "name"]


def test_dispatch_routes_archive(tmp_path):
    p = _zip(tmp_path / "d.zip", {"d.csv": _CSV})
    assert inspect(p).path.endswith("d.zip::d.csv")


def test_cli_inspect_archive(tmp_path):
    from click.testing import CliRunner

    from micromap_mapforge.cli import main

    p = _zip(tmp_path / "supp.zip", {"taxa.csv": _CSV, "more.tsv": b"x\ty\n1\t2\n"})
    runner = CliRunner()
    res = runner.invoke(main, ["inspect", str(p), "--out", str(tmp_path / "out")])
    assert res.exit_code == 0
    report = (tmp_path / "out" / "inspection-report.md").read_text(encoding="utf-8")
    assert "**Note:**" in report and "taxa.csv" in report
    res2 = runner.invoke(main, ["inspect", str(p), "--member", "taxa.csv",
                                "--out", str(tmp_path / "out2")])
    assert res2.exit_code == 0
    assert "`a`" in (tmp_path / "out2" / "inspection-report.md").read_text(encoding="utf-8")
