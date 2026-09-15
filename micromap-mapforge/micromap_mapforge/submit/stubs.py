"""Destination executor stubs.

Only `RegistryOnlyExecutor` remains. `NewInstanceExecutor` graduated out of
stubs in #57 — see micromap_mapforge/submit/new_instance.py.
"""

from ..emit.bundle import IngestBundle
from .base import DryRunReport, SubmissionReceipt


class RegistryOnlyExecutor:
    name = "registry-only"

    def dry_run(self, bundle: IngestBundle) -> DryRunReport:
        return DryRunReport(
            destination=self.name,
            would_write_nodes=0,
            would_write_relationships=0,
            notes="registry-only dry run — no writes in M2 (Contribution node deferred to M3)",
        )

    def submit(self, bundle: IngestBundle) -> SubmissionReceipt:
        return SubmissionReceipt(
            destination=self.name,
            success=True,
            notes="registry-only submit — Contribution node recording deferred to M3",
        )
