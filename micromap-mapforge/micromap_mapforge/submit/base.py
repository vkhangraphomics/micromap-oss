"""DestinationExecutor protocol and shared dataclasses."""

from dataclasses import dataclass
from typing import Protocol

from ..emit.bundle import IngestBundle


@dataclass
class DryRunReport:
    destination: str
    would_write_nodes: int
    would_write_relationships: int
    notes: str = ""


@dataclass
class SubmissionReceipt:
    destination: str
    success: bool
    nodes_written: int = 0
    relationships_written: int = 0
    notes: str = ""


class DestinationExecutor(Protocol):
    name: str

    def dry_run(self, bundle: IngestBundle) -> DryRunReport:
        ...

    def submit(self, bundle: IngestBundle) -> SubmissionReceipt:
        ...
