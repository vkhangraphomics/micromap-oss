"""F2 (#79): the resolver preload driver must suppress UNRECOGNIZED-class
notifications.

`build_resolvers` runs `MATCH (n:Label)` for every in-scope project label.
On a partly-populated graph, a label that's referenced by the bundle but
absent from the graph still emits an `01N42` UNRECOGNIZED notification per
query. #117 narrowed *which* labels are queried, but a referenced-yet-absent
label still floods stderr and buries real warnings. The fix F2 asks for is to
silence that notification class at the driver layer.
"""

from unittest.mock import MagicMock

from neo4j import GraphDatabase

from micromap_mapforge import cli


def _suppression_kwarg_items() -> dict:
    """The single kwarg the helper emits, whatever its version-dependent name."""
    kwargs = cli._notification_suppression_kwargs()
    assert len(kwargs) == 1, f"expected exactly one suppression kwarg, got {kwargs!r}"
    return kwargs


def test_suppression_kwargs_target_unrecognized_classification():
    """The helper disables the UNRECOGNIZED notification class — the class that
    covers unknown-label (`01N42`) notifications — and nothing broader."""
    (name, value), = _suppression_kwarg_items().items()
    assert name in (
        "notifications_disabled_classifications",  # neo4j >= 5.22
        "notifications_disabled_categories",       # neo4j 5.15 - 5.21
    ), f"unexpected suppression kwarg name: {name}"
    assert [v.name for v in value] == ["UNRECOGNIZED"]


def test_suppression_kwargs_are_accepted_by_the_real_driver():
    """Guard against the kwarg name drifting out from under the installed
    driver: construction is lazy, so this validates the key without a server."""
    driver = GraphDatabase.driver(
        "bolt://localhost:7687", auth=("x", "y"),
        **cli._notification_suppression_kwargs(),
    )
    driver.close()


def test_build_resolvers_passes_suppression_kwargs_to_driver_factory():
    """`_build_resolvers` must build its Neo4j driver with notification
    suppression applied — otherwise the resolve path still floods stderr."""
    fake_driver = MagicMock()
    factory = MagicMock(return_value=fake_driver)

    cli._build_resolvers(
        "bolt://host:7687", "neo4j", "pw", "neo4j",
        labels_of_interest=set(),  # preload nothing — keep the mock driver idle
        driver_factory=factory,
    )

    assert factory.call_count == 1
    _, kwargs = factory.call_args
    expected = cli._notification_suppression_kwargs()
    for key, value in expected.items():
        assert kwargs.get(key) == value, (
            f"_build_resolvers did not pass {key} to the driver factory; "
            f"got kwargs={kwargs!r}"
        )
