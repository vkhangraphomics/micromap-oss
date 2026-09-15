from unittest.mock import MagicMock

import pytest

from micromap_mapforge.submit.core import MicroMapCoreExecutor
from micromap_mapforge.submit.factory import executor_for_destination
from micromap_mapforge.submit.new_instance import NewInstanceExecutor
from micromap_mapforge.submit.stubs import RegistryOnlyExecutor


def test_factory_returns_core_for_core_destination():
    driver = MagicMock()
    ex = executor_for_destination("micromap-core", driver=driver)
    assert isinstance(ex, MicroMapCoreExecutor)


def test_factory_returns_registry_stub():
    ex = executor_for_destination("registry-only")
    assert isinstance(ex, RegistryOnlyExecutor)


def test_factory_returns_new_instance_executor():
    ex = executor_for_destination("new-federated-instance")
    assert isinstance(ex, NewInstanceExecutor)


def test_factory_raises_on_unknown_destination():
    with pytest.raises(ValueError, match="unknown destination"):
        executor_for_destination("quantum-graph-cloud")
