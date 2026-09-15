"""License-aware import enforcement (C6.1, #76).

A deployment declares a ``LicensePolicy`` (chiefly: is this a **commercial**
deployment?). ``evaluate`` then gates a catalog KG against it:

- **Non-commercial deployment** → permitted (academic use of the catalogued KGs
  is free); only an explicit ``denylist`` blocks.
- **Commercial deployment** → permitted only for KGs whose ``commercial_use`` is
  ``allowed`` (CC0 / CC-BY / CC-BY-SA / MIT / public domain). ``restricted``
  (needs per-resource clearance), ``prohibited`` (needs a paid license, e.g.
  DrugBank / KEGG), and ``unknown`` are **refused** unless explicitly opted into
  (``allow_restricted`` / ``allow_unknown``) or the license is on the
  ``allowlist``.

The catalog's license fields are best-effort (see the catalog ``_caveat``); this
gate is a guardrail, not legal clearance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import KgSource


@dataclass(frozen=True)
class LicensePolicy:
    commercial: bool = False           # does this deployment make commercial use?
    allow_restricted: bool = False     # permit 'restricted' KGs (cleared per-resource)?
    allow_unknown: bool = False        # permit 'unknown' commercial posture?
    allowlist: frozenset[str] = field(default_factory=frozenset)  # SPDX licenses always OK
    denylist: frozenset[str] = field(default_factory=frozenset)   # catalog ids always blocked


@dataclass(frozen=True)
class LicenseDecision:
    allowed: bool
    reason: str


def evaluate(source: KgSource, policy: LicensePolicy) -> LicenseDecision:
    """Decide whether ``source`` may be imported under ``policy``."""
    if source.id in policy.denylist:
        return LicenseDecision(False, f"{source.id} is blocked by the policy denylist")

    if not policy.commercial:
        return LicenseDecision(True, "non-commercial deployment: academic use permitted")

    # Commercial deployment from here on.
    if source.license in policy.allowlist:
        return LicenseDecision(True, f"license {source.license} is in the policy allowlist")

    cu = source.commercial_use
    if cu == "allowed":
        return LicenseDecision(True, f"{source.license} permits commercial use")
    if cu == "restricted":
        reason = f"{source.name}: commercial use restricted — {source.license_notes}"
        return LicenseDecision(policy.allow_restricted, reason)
    if cu == "unknown":
        reason = f"{source.name}: commercial-use posture unknown — verify before import"
        return LicenseDecision(policy.allow_unknown, reason)
    # prohibited
    return LicenseDecision(
        False,
        f"{source.name}: commercial use requires a paid license — {source.license_notes}",
    )


def load_policy(path: str | Path) -> LicensePolicy:
    """Load a deployment license manifest (YAML)."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return LicensePolicy(
        commercial=bool(data.get("commercial", False)),
        allow_restricted=bool(data.get("allow_restricted", False)),
        allow_unknown=bool(data.get("allow_unknown", False)),
        allowlist=frozenset(data.get("allowlist", []) or []),
        denylist=frozenset(data.get("denylist", []) or []),
    )
