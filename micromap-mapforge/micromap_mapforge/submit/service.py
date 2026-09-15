"""Shared submit core: submit_bundle() + record builders.

The single source of truth for "submit an approved bundle to a given Neo4j
driver." Both the `mapforge submit` CLI (micromap-core branch) and the HTTP
contribution route call this, so they produce identical graph + provenance.

Unlike NewInstanceExecutor, this never opens its own driver and never reads the
bundle's federation block — the caller supplies the driver and the write is
always core-style (MicroMapCoreExecutor). Routing/target authority lives with
the caller.
"""
from __future__ import annotations

import hashlib
import json
import yaml
from datetime import datetime, timezone
from pathlib import Path

from ..emit.bundle import load_bundle
from ..provenance.contribution import ContributionRecord, write_contribution
from ..provenance.decision import DecisionRecord, write_decision
from .base import SubmissionReceipt
from .core import MicroMapCoreExecutor


class SubmitError(Exception):
    """Base for submit_bundle precondition failures."""


class BundleManifestMissingError(SubmitError):
    """No manifest.json — bundle was never emitted."""


class BundleNotApprovedError(SubmitError):
    """Bundle is not approved and force was not set."""


class MissingReviewerError(SubmitError):
    """provenance.enabled but no reviewer available."""


class MissingSourceShaError(SubmitError):
    """source_sha256 not sealed and source file absent."""


def resolve_source_sha(bundle_dir: Path, mapping: dict) -> str:
    """Prefer the sealed mapping.yaml::source.sha256 (E5/#78); fall back to
    recomputing from the on-disk source file; raise MissingSourceShaError when
    neither is available (an empty sha corrupts the Contribution audit trail via
    MERGE-collision)."""
    source_block = mapping.get("source") or {}
    sealed = source_block.get("sha256")
    if sealed:
        return sealed
    raw_path = source_block.get("path")
    if not raw_path:
        raise MissingSourceShaError(
            "source_sha256 not sealed and mapping.yaml has no source.path to recompute from"
        )
    source_path = Path(raw_path)
    if not source_path.is_absolute():
        source_path = bundle_dir / source_path
    if source_path.exists():
        return hashlib.sha256(source_path.read_bytes()).hexdigest()
    raise MissingSourceShaError(
        f"source_sha256 not sealed in mapping.yaml and source file at "
        f"{source_path} is absent; refusing to write empty source_sha256"
    )


def build_contribution_record(
    bundle_dir: Path, routing: dict, reviewer: str, receipt,
    *, force_submitted: bool | None = None,
) -> ContributionRecord:
    """Pure builder: bundle + routing + receipt -> ContributionRecord.
    Extracted verbatim from cli.py::_write_contribution_for_bundle."""
    mapping_bytes = (bundle_dir / "mapping.yaml").read_bytes()
    mapping_sha = hashlib.sha256(mapping_bytes).hexdigest()
    mapping = yaml.safe_load(mapping_bytes.decode("utf-8"))
    source_sha = resolve_source_sha(bundle_dir, mapping)
    resolution = json.loads((bundle_dir / "resolution.json").read_text(encoding="utf-8"))
    src = (mapping.get("source") or {})
    return ContributionRecord(
        contributor=routing.get("provenance", {}).get("contributor", routing["organization_id"]),
        reviewer=reviewer,
        organization_id=routing["organization_id"],
        source_sha256=source_sha,
        mapping_sha256=mapping_sha,
        destination=receipt.destination,
        submitted_at=datetime.now(timezone.utc),
        resolved_count=resolution.get("resolved_count", 0),
        unresolved_count=resolution.get("unresolved_count", 0),
        ambiguous_count=resolution.get("ambiguous_count", 0),
        source_name=src.get("name"),
        source_license=src.get("license"),
        source_url=src.get("url"),
        source_doi=src.get("doi"),
        source_contact=src.get("contact"),
        source_version=src.get("version"),
        source_accessed_at=src.get("accessed_at"),
        source_ethics_ref=src.get("ethics_ref"),
        force_submitted=force_submitted,
    )


_DECISION_SUBJECT_LABELS = ("Disease", "TherapeuticArea", "Target", "Study")


def derive_about_resolved(bundle_dir: Path, cap: int = 25) -> list[dict]:
    """Subject entities from resolution.json as {label, field, value} ABOUT rows.
    Extracted from cli.py::_derive_about_resolved."""
    res_path = bundle_dir / "resolution.json"
    if not res_path.is_file():
        return []
    data = json.loads(res_path.read_text(encoding="utf-8"))
    out: list[dict] = []
    seen: set = set()
    for row in data.get("resolved", []):
        label = row.get("entity_label")
        if label not in _DECISION_SUBJECT_LABELS:
            continue
        cands = row.get("candidates") or []
        if not cands:
            continue
        c = cands[0]
        field_name, value = c.get("merge_field"), c.get("merge_value")
        if not field_name or value is None:
            continue
        key = (label, field_name, str(value))
        if key in seen:
            continue
        seen.add(key)
        out.append({"label": label, "field": field_name, "value": str(value)})
        if len(out) >= cap:
            break
    return out


def build_decision_record(
    bundle_dir: Path, routing: dict, receipt,
    *, about_tags: list[str], summary: str | None, rationale: str, actor: str | None,
) -> DecisionRecord:
    """Pure builder for the data_contribution :Decision. Extracted from
    cli.py::_write_decision_for_bundle."""
    mapping_bytes = (bundle_dir / "mapping.yaml").read_bytes()
    mapping_sha = hashlib.sha256(mapping_bytes).hexdigest()
    mapping = yaml.safe_load(mapping_bytes.decode("utf-8"))
    source_sha = resolve_source_sha(bundle_dir, mapping)
    src = (mapping.get("source") or {})
    source_name = src.get("name") or bundle_dir.name
    resolution = json.loads((bundle_dir / "resolution.json").read_text(encoding="utf-8"))
    resolved_count = resolution.get("resolved_count", 0)
    unresolved_count = resolution.get("unresolved_count", 0)
    ambiguous_count = resolution.get("ambiguous_count", 0)
    org_id = routing["organization_id"]
    contributor = routing.get("provenance", {}).get("contributor", org_id)
    default_summary = (
        f"MapForge ingest: {source_name} "
        f"({resolved_count} resolved, {unresolved_count} unresolved, "
        f"{receipt.nodes_written} nodes / {receipt.relationships_written} rels written)"
    )
    evidence = [f"mapforge:contribution/{mapping_sha[:12]}"]
    if src.get("url"):
        evidence.append(str(src["url"]))
    if src.get("doi"):
        evidence.append(f"doi:{src['doi']}")
    return DecisionRecord(
        id=f"dec-contrib-{mapping_sha[:16]}",
        organization_id=org_id,
        occurred_at=datetime.now(timezone.utc),
        summary=summary or default_summary,
        rationale=rationale or "",
        actor_user=actor or contributor,
        actor_org=org_id,
        role="service",
        evidence_refs=evidence,
        entity_tags=list(about_tags),
        about_resolved=derive_about_resolved(bundle_dir),
        contribution_mapping_sha256=mapping_sha,
        contribution_source_sha256=source_sha,
        resolved_count=resolved_count,
        unresolved_count=unresolved_count,
        ambiguous_count=ambiguous_count,
    )


def submit_bundle(
    bundle_dir: Path,
    *,
    driver,
    database: str,
    reviewer: str | None = None,
    force: bool = False,
    write_decision_record: bool = True,
    about_tags: tuple[str, ...] = (),
    decision_summary: str | None = None,
    rationale: str = "",
    actor: str | None = None,
    on_warning=None,
) -> SubmissionReceipt:
    """Gate, then core-write the bundle against `driver`/`database`.

    Writes a Contribution provenance node when routing.provenance.enabled
    (default True). Raises MissingReviewerError when provenance is enabled
    but no reviewer is provided — checked BEFORE the data write (fail-closed).

    The effective reviewer is resolved as: ``reviewer`` arg → manifest
    ``reviewer`` field → error.  An approved bundle whose manifest.json
    carries ``"reviewer"`` can therefore be submitted with ``reviewer=None``
    and still succeed.
    """
    bundle_dir = Path(bundle_dir)
    b = load_bundle(bundle_dir)
    if b.manifest is None:
        raise BundleManifestMissingError(f"no manifest.json in {bundle_dir}")
    if not b.manifest.get("approved", False) and not force:
        raise BundleNotApprovedError(f"bundle at {bundle_dir} is not approved")

    routing = yaml.safe_load((bundle_dir / "routing.yaml").read_text(encoding="utf-8"))
    provenance_enabled = (routing.get("provenance") or {}).get("enabled", True)

    # Resolve effective reviewer: arg > manifest field.  Gate BEFORE any write.
    effective_reviewer = reviewer or (b.manifest or {}).get("reviewer")
    if provenance_enabled and not effective_reviewer:
        raise MissingReviewerError("provenance.enabled but no reviewer provided")

    receipt = MicroMapCoreExecutor(driver=driver, database=database).submit(b)

    if provenance_enabled:
        force_submitted_flag = True if (not b.manifest.get("approved", False) and force) else None
        record = build_contribution_record(
            bundle_dir, routing, effective_reviewer, receipt, force_submitted=force_submitted_flag,
        )
        write_contribution(driver, record, database=database)
        if write_decision_record:
            try:
                record_d = build_decision_record(
                    bundle_dir, routing, receipt,
                    about_tags=list(about_tags), summary=decision_summary,
                    rationale=rationale, actor=actor,
                )
                write_decision(driver, record_d, database=database)
            except Exception as exc:  # non-gating: data + Contribution already written
                if on_warning is not None:
                    on_warning(f"decision provenance not recorded ({type(exc).__name__}: {exc})")
    return receipt
