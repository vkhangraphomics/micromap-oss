"""NewInstanceExecutor — Bolt-write path for new constituent databases.

Reads the federation block from the bundle's routing.yaml, then writes the
bundle's Cypher directly to the constituent Neo4j via Bolt.

Registration with a Fabric composite database is a separate step:
  mapforge register-constituent ...
See issue #189.
"""

from __future__ import annotations

import os
from typing import Any, Callable

import yaml
from neo4j import GraphDatabase

from ..emit.bundle import IngestBundle
from .base import DryRunReport, SubmissionReceipt
from .core import MicroMapCoreExecutor


class NewInstanceExecutor:
    name = "new-federated-instance"

    def __init__(self, *, driver_factory: Callable = GraphDatabase.driver):
        self.driver_factory = driver_factory

    def _read_federation_block(self, bundle: IngestBundle) -> dict[str, Any]:
        routing_path = bundle.routing_path
        if not routing_path.is_file():
            raise ValueError(f"bundle has no routing.yaml at {routing_path}")
        routing = yaml.safe_load(routing_path.read_text(encoding="utf-8")) or {}
        block = (routing.get("destinations", {})
                 .get("new-federated-instance", {})
                 .get("federation"))
        if not block:
            raise ValueError(
                "routing.yaml is missing destinations.new-federated-instance.federation"
            )
        return block

    def _resolve_bolt_password(self, ref: str) -> str:
        if not ref.startswith("env:"):
            raise ValueError(f"bolt_auth_ref must start with 'env:', got {ref!r}")
        name = ref[4:]
        value = os.environ.get(name)
        if not value:
            raise EnvironmentError(
                f"env var {name!r} is not set or empty (referenced by federation.bolt_auth_ref)"
            )
        return value

    def dry_run(self, bundle: IngestBundle) -> DryRunReport:
        fed = self._read_federation_block(bundle)
        self._resolve_bolt_password(fed["bolt_auth_ref"])  # prove env is set
        local = MicroMapCoreExecutor(driver=None).dry_run(bundle)
        return DryRunReport(
            destination=self.name,
            would_write_nodes=local.would_write_nodes,
            would_write_relationships=local.would_write_relationships,
            notes=f"would write to {fed['bolt_uri']}",
        )

    def submit(self, bundle: IngestBundle) -> SubmissionReceipt:
        fed = self._read_federation_block(bundle)
        bolt_password = self._resolve_bolt_password(fed["bolt_auth_ref"])
        bolt_user = fed.get("bolt_user", "mapforge")
        driver = None
        try:
            driver = self.driver_factory(fed["bolt_uri"], auth=(bolt_user, bolt_password))
            write = MicroMapCoreExecutor(driver).submit(bundle)
        finally:
            if driver is not None:
                driver.close()
        return SubmissionReceipt(
            destination=self.name,
            success=write.success,
            nodes_written=write.nodes_written,
            relationships_written=write.relationships_written,
            notes=f"wrote to {fed['bolt_uri']}",
        )
