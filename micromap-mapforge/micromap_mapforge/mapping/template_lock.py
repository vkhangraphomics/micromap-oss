"""#152/#153 foundation: version-discipline lock for the built-in templates.

Each template is pinned in ``templates/versions.lock`` as ``{version, sha256}``,
where ``sha256`` is a canonical hash of the template body **excluding** the
``version`` field. The guard test asserts the live templates match the lock; the
regenerator (``python -m micromap_mapforge.mapping.template_lock``) refuses to
record a body change under an unchanged version — so a template can never change
without its ``version:`` being bumped, which is what gives a future ``templates
diff`` (#152) / migration (#153) a real artifact to work with.

The body hash is canonical JSON (sorted keys, version stripped): stable across
line-ending and formatting differences, and it flags *semantic* changes, not
whitespace. Comment-only edits do not count as a change (comments carry no
semantics the mapper reads).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
LOCK_PATH = TEMPLATES_DIR / "versions.lock"


class LockDisciplineError(Exception):
    """A template's body changed but its ``version:`` did not — bump it."""


def _template_files() -> list[Path]:
    return sorted(p for p in TEMPLATES_DIR.glob("*.yaml"))


def _body_sha256(payload: dict[str, Any]) -> str:
    """Canonical hash of the template body, with ``version`` removed so a pure
    version bump does not look like a body change (and vice-versa)."""
    body = {k: v for k, v in payload.items() if k != "version"}
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _state_for(path: Path) -> dict[str, str]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {"version": str(payload.get("version", "")), "sha256": _body_sha256(payload)}


def current_states() -> dict[str, dict[str, str]]:
    """``{template_name: {version, sha256}}`` for the live template files.
    ``template_name`` is the file stem (``microbiome``, ``genomics``, …)."""
    return {p.stem: _state_for(p) for p in _template_files()}


def load_lock() -> dict[str, dict[str, str]]:
    if not LOCK_PATH.exists():
        return {}
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def build_lock(
    current: dict[str, dict[str, str]],
    existing: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    """The new lock from ``current``, enforcing bump-on-body-change against
    ``existing``. Raises :class:`LockDisciplineError` naming every template whose
    body changed while its version stayed the same."""
    offenders = [
        name for name, state in current.items()
        if name in existing
        and existing[name].get("sha256") != state["sha256"]
        and existing[name].get("version") == state["version"]
    ]
    if offenders:
        raise LockDisciplineError(
            "template body changed without a version bump: "
            + ", ".join(f"{n} (still {current[n]['version']})" for n in sorted(offenders))
            + ". Bump `version:` (minor for additive, major for breaking), then "
            "regenerate: python -m micromap_mapforge.mapping.template_lock"
        )
    return dict(current)


def write_lock(lock: dict[str, dict[str, str]]) -> None:
    # newline="\n": the repo forces LF (.gitattributes); writing LF here keeps the
    # regenerated lock byte-identical to what git stores, on Windows and Linux alike.
    LOCK_PATH.write_text(
        json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


def main() -> None:
    """Regenerate versions.lock from the live templates, enforcing the bump rule.
    Exits non-zero (with a clean message, no traceback) on a discipline violation."""
    import sys
    try:
        lock = build_lock(current_states(), load_lock())
    except LockDisciplineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    write_lock(lock)
    print(f"wrote {LOCK_PATH.name}: {len(lock)} templates")
    for name in sorted(lock):
        print(f"  {name}: v{lock[name]['version']}  {lock[name]['sha256'][:12]}")


if __name__ == "__main__":
    main()
