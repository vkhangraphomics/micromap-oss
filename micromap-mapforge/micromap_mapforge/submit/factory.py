"""Pick the right DestinationExecutor for a routing.yaml destination string."""

from .base import DestinationExecutor
from .core import MicroMapCoreExecutor
from .new_instance import NewInstanceExecutor
from .stubs import RegistryOnlyExecutor


def executor_for_destination(
    destination: str,
    *,
    driver=None,
    database: str = "neo4j",
) -> DestinationExecutor:
    if destination == "micromap-core":
        if driver is None:
            raise ValueError("MicroMapCoreExecutor requires a Neo4j driver")
        return MicroMapCoreExecutor(driver=driver, database=database)
    if destination == "registry-only":
        return RegistryOnlyExecutor()
    if destination == "new-federated-instance":
        return NewInstanceExecutor()
    raise ValueError(f"unknown destination: {destination!r}")
