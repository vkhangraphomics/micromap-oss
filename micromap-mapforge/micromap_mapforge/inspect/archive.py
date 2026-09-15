"""Archive expansion for the inspector — C4 (#76).

``.zip`` / ``.tar.gz`` / ``.tgz`` / ``.tar`` / ``.tar.bz2`` / ``.tar.xz`` are
extracted to a temp dir, the inspectable members are discovered, and one is
profiled (the first by default; ``member=`` / ``--member`` picks another). Each
member is a candidate source, so a multi-file archive records the inventory in
``SourceProfile.note`` — mirroring the multi-sheet xlsx behaviour.

Extraction is untrusted-input-safe: zip-slip guarded, member-count and total
uncompressed size capped, tar via the ``data`` filter.
"""
from __future__ import annotations

import os
import tarfile
import tempfile
import zipfile
from dataclasses import replace
from pathlib import Path

from .types import SourceProfile

# Mirrors integration/biocypher/runner.py::_ARCHIVE_SUFFIXES.
ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".tar", ".zip")

_MAX_TOTAL_BYTES = 500 * 1024 * 1024   # 500 MB uncompressed
_MAX_MEMBERS = 5000


class UnsafeArchiveError(ValueError):
    """A member escapes the extraction dir or is a non-regular entry."""


class ArchiveTooLargeError(ValueError):
    """The archive exceeds the member-count or total-size cap."""


def is_archive(path: str | Path) -> bool:
    name = Path(path).name.lower()
    return any(name.endswith(sfx) for sfx in ARCHIVE_SUFFIXES)


def _safe_extract(path: Path, dest: Path) -> None:
    dest = dest.resolve()
    if path.name.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            infos = [i for i in z.infolist() if not i.is_dir()]
            if len(infos) > _MAX_MEMBERS:
                raise ArchiveTooLargeError(f"{path}: {len(infos)} members exceeds {_MAX_MEMBERS}")
            if sum(i.file_size for i in infos) > _MAX_TOTAL_BYTES:
                raise ArchiveTooLargeError(f"{path}: uncompressed size exceeds {_MAX_TOTAL_BYTES}")
            for i in infos:
                target = (dest / i.filename).resolve()
                if target != dest and not str(target).startswith(str(dest) + os.sep):
                    raise UnsafeArchiveError(f"{path}: member escapes archive dir: {i.filename}")
            z.extractall(dest)  # noqa: S202 — members validated for zip-slip above
        return

    try:
        with tarfile.open(path) as tar:
            members = tar.getmembers()
            if len(members) > _MAX_MEMBERS:
                raise ArchiveTooLargeError(f"{path}: {len(members)} members exceeds {_MAX_MEMBERS}")
            if sum(max(m.size, 0) for m in members) > _MAX_TOTAL_BYTES:
                raise ArchiveTooLargeError(f"{path}: uncompressed size exceeds {_MAX_TOTAL_BYTES}")
            # 'data' filter (3.12+) blocks path traversal + special files.
            tar.extractall(dest, filter="data")
    except tarfile.TarError as exc:
        raise UnsafeArchiveError(f"{path}: not a valid archive ({exc})") from exc


def _inspectable_members(root: Path, exts: set[str]) -> list[str]:
    out = [
        p.relative_to(root).as_posix()
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.suffix.lower() in exts
    ]
    return out


def archive_members(path: str | Path) -> list[str]:
    """Inspectable members inside the archive (posix relative paths, sorted)."""
    from .dispatch import supported_extensions

    with tempfile.TemporaryDirectory(prefix="mf-archive-") as tmp:
        _safe_extract(Path(path), Path(tmp))
        return _inspectable_members(Path(tmp), supported_extensions())


def inspect_archive(path: str | Path, member: str | None = None,
                    sheet: str | None = None, table: int | None = None) -> SourceProfile:
    """Expand an archive and profile one inspectable member (the first by
    default). Raises ``ValueError`` for an empty/invalid archive or unknown member."""
    from .dispatch import inspect as _inspect
    from .dispatch import supported_extensions

    path = Path(path)
    with tempfile.TemporaryDirectory(prefix="mf-archive-") as tmp:
        root = Path(tmp)
        _safe_extract(path, root)
        members = _inspectable_members(root, supported_extensions())
        if not members:
            raise ValueError(
                f"{path}: archive contains no inspectable sources "
                f"({sorted(supported_extensions())})"
            )
        if member is not None:
            if member not in members:
                raise ValueError(f"{path}: no inspectable member {member!r}. Members: {members}")
            target = member
        else:
            target = members[0]
        inner = _inspect(root / target, sheet=sheet, table=table)

    note = ""
    if len(members) > 1 and member is None:
        others = [m for m in members if m != target]
        note = (
            f"archive has {len(members)} inspectable members {members}; "
            f"inspected {target!r}. Pick another with `--member <name>` "
            f"(e.g. {others[0]!r})."
        )
    if inner.note:
        note = f"{note} | {inner.note}" if note else inner.note

    return replace(inner, path=f"{path}::{target}", note=note)
