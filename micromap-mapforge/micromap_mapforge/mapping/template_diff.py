"""#152: `mapforge templates diff` — semantic diff between two versions of a
built-in discipline template.

Templates are single-file, version bumped in place (see ``template_lock.py``) —
there's no ``<name>/<version>.yaml`` archive. A prior version's body is instead
resolved from git history: the last commit where the template's ``version:``
field equalled the requested value. The *current* (live) version is read
straight off disk — no git call, and it works against uncommitted edits.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import yaml

from .template_lock import TEMPLATES_DIR

__all__ = ["VersionNotFoundError", "resolve_version_payload", "semantic_diff", "TEMPLATES_DIR"]


class VersionNotFoundError(Exception):
    """Neither the live file nor any commit in its history carries the
    requested template version."""


def _git(*args: str, cwd: Path) -> str:
    # encoding="utf-8" explicitly: text=True alone decodes with the platform's
    # default locale codec, which on Windows is typically NOT UTF-8 — any
    # non-ASCII byte in a template's notes (an em-dash, say) then decodes
    # differently here than in the live-file fast path's explicit UTF-8 read,
    # producing a false diff between two commits with byte-identical content.
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True,
        encoding="utf-8",
    )
    return result.stdout


def _repo_root(path: Path) -> Path:
    return Path(_git("rev-parse", "--show-toplevel", cwd=path.parent).strip())


def _is_shallow(repo_path: Path) -> bool:
    """Whether the git checkout at (or above) ``repo_path`` is shallow (e.g.
    ``git clone --depth 1``, or CI's default checkout). A shallow clone is
    missing whole commits, not just failing to match one — walking its log
    can't tell those two cases apart on its own, so callers use this to give
    an actionable message instead of a false "no such version"."""
    out = _git("rev-parse", "--is-shallow-repository", cwd=repo_path)
    return out.strip() == "true"


def resolve_version_payload(name: str, version: str) -> dict[str, Any]:
    """The parsed YAML body of template ``name`` as it was at ``version``."""
    path = TEMPLATES_DIR / f"{name}.yaml"
    if not path.exists():
        raise VersionNotFoundError(f"no such template: {name!r}")

    current = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if str(current.get("version", "")) == version:
        return current

    repo_root = _repo_root(path)
    rel = path.relative_to(repo_root).as_posix()
    log = _git("log", "--format=%H", "--reverse", "--", rel, cwd=repo_root)

    match: dict[str, Any] | None = None
    for sha in (line for line in log.splitlines() if line):
        blob = _git("show", f"{sha}:{rel}", cwd=repo_root)
        payload = yaml.safe_load(blob) or {}
        if str(payload.get("version", "")) == version:
            match = payload

    if match is None:
        msg = f"no commit (or the live file) has {name!r} at version {version!r}"
        if _is_shallow(repo_root):
            msg += (
                " in the available git history — this is a shallow clone, so "
                "the commit may simply be missing rather than nonexistent; run "
                "`git fetch --unshallow` and retry before concluding otherwise"
            )
        raise VersionNotFoundError(msg)
    return match


def semantic_diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Added/removed/changed classes between two template payloads."""
    old_classes: dict[str, Any] = old.get("classes") or {}
    new_classes: dict[str, Any] = new.get("classes") or {}

    added = sorted(set(new_classes) - set(old_classes))
    removed = sorted(set(old_classes) - set(new_classes))

    changed: dict[str, Any] = {}
    for cls_name in sorted(set(old_classes) & set(new_classes)):
        o, n = old_classes[cls_name] or {}, new_classes[cls_name] or {}
        if o == n:
            continue

        o_x, n_x = o.get("x_mapforge") or {}, n.get("x_mapforge") or {}
        o_ids, n_ids = set(o_x.get("identifiers") or []), set(n_x.get("identifiers") or [])
        ids_added = sorted(n_ids - o_ids)
        ids_removed = sorted(o_ids - n_ids)

        entry: dict[str, Any] = {}
        if ids_added:
            entry["identifiers_added"] = ids_added
        if ids_removed:
            entry["identifiers_removed"] = ids_removed
        if o_x.get("notes") != n_x.get("notes"):
            entry["notes_changed"] = True
        other = sorted(
            k for k in set(o) | set(n)
            if k != "x_mapforge" and o.get(k) != n.get(k)
        )
        if other:
            entry["other_fields_changed"] = other
        changed[cls_name] = entry

    return {"classes_added": added, "classes_removed": removed, "classes_changed": changed}
