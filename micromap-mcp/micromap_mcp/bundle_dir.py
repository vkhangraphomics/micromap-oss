"""Per-session bundle directories under a shared base, with 7-day GC.
`resolve` is path-traversal safe."""
from __future__ import annotations
import json
import os
import shutil
import stat
import time
import uuid
from pathlib import Path

#: Ownership record written into each bundle at create time (#315). Absence
#: means a pre-#315 legacy bundle — callers treat those as default-org-owned.
OWNER_FILE = ".owner.json"


class BundleDirManager:
    def __init__(self, base: str, gc_age_days: int = 7):
        self._base = Path(base).resolve()
        self._base.mkdir(parents=True, exist_ok=True)
        self._gc_age_seconds = gc_age_days * 86400

    @property
    def base(self) -> Path:
        return self._base

    def create_session(self, owner_org: str | None = None, owner_user: str = "") -> str:
        path = self._base / uuid.uuid4().hex
        path.mkdir(parents=True, exist_ok=False)
        if owner_org:
            (path / OWNER_FILE).write_text(
                json.dumps({"org": owner_org, "user": owner_user}),
                encoding="utf-8",
            )
        return str(path)

    def owner_org(self, bundle_dir: Path) -> str | None:
        """Org recorded at create_session, or None for a pre-#315 bundle.

        An unreadable/corrupt record also returns None — the caller then
        treats the bundle as default-org-owned, which only ever narrows
        access toward the first-party org, never widens it to the corrupter.
        """
        record = Path(bundle_dir) / OWNER_FILE
        if not record.is_file():
            return None
        try:
            return json.loads(record.read_text(encoding="utf-8")).get("org") or None
        except (OSError, ValueError):
            return None

    def contain(self, path: str, *, kind: str = "path") -> Path:
        """Validate that path lies inside the base. Returns absolute path.

        Relative paths resolve against the base. Unlike resolve(), the base
        itself is accepted — this is containment for files handed to tools
        (#316), not bundle-dir lookup.
        """
        p = (self._base / path).resolve() if not os.path.isabs(path) else Path(path).resolve()
        try:
            p.relative_to(self._base)
        except ValueError as e:
            raise ValueError(f"{kind} escapes base: {path!r}") from e
        return p

    def resolve(self, bundle_dir: str) -> Path:
        """Validate that bundle_dir is inside the base. Returns absolute path."""
        p = self.contain(bundle_dir, kind="bundle_dir")
        if p == self._base:
            raise ValueError(
                f"bundle_dir must be a session subdirectory, not the base itself: {bundle_dir!r}"
            )
        return p

    def gc(self) -> list[str]:
        """Remove sessions whose mtime is older than gc_age_seconds. Returns
        the paths removed."""
        now = time.time()
        removed: list[str] = []
        for child in self._base.iterdir():
            if not child.is_dir():
                continue
            if now - child.stat().st_mtime > self._gc_age_seconds:
                _rm_tree(child)
                removed.append(str(child))
        return removed


def _rm_tree(path: Path) -> None:
    """Recursive removal that handles read-only files (Windows umask) and
    does not follow top-level symlinks."""
    def _on_error(func, p, exc_info):
        # Likely read-only on Windows; clear the bit and retry the failing op.
        os.chmod(p, stat.S_IWRITE)
        func(p)
    shutil.rmtree(path, onerror=_on_error)
