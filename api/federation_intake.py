"""Zip-slip-safe, size-capped extraction of an uploaded bundle tarball."""
from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path

_DEFAULT_MAX_TOTAL = 100 * 1024 * 1024   # 100 MB uncompressed
_DEFAULT_MAX_MEMBERS = 2000


class UnsafeBundleError(Exception):
    """A tar member escapes the destination dir or is a non-regular entry."""


class BundleTooLargeError(Exception):
    """The bundle exceeds member-count or total-size caps."""


def safe_extract_bundle(
    tar_bytes: bytes,
    dest_dir: str | Path,
    *,
    max_total_bytes: int = _DEFAULT_MAX_TOTAL,
    max_members: int = _DEFAULT_MAX_MEMBERS,
) -> None:
    dest = Path(dest_dir).resolve()
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
            members = tar.getmembers()
            if len(members) > max_members:
                raise BundleTooLargeError(f"{len(members)} members exceeds cap {max_members}")
            total = 0
            for m in members:
                if not (m.isfile() or m.isdir()):
                    raise UnsafeBundleError(f"disallowed tar entry type: {m.name}")
                target = (dest / m.name).resolve()
                if not str(target).startswith(str(dest) + os.sep) and target != dest:
                    raise UnsafeBundleError(f"path escapes bundle dir: {m.name}")
                total += max(m.size, 0)
                if total > max_total_bytes:
                    raise BundleTooLargeError(f"uncompressed size exceeds cap {max_total_bytes}")
            tar.extractall(dest, members=members)  # noqa: S202 — members validated above
    except (BundleTooLargeError, UnsafeBundleError):
        raise
    except tarfile.TarError as exc:
        raise UnsafeBundleError(f"not a valid gzip tar: {exc}") from exc
