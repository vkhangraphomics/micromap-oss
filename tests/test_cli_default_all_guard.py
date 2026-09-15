"""A standalone maintenance flag must NOT silently trigger --all.

Running `--migrate-duplicate-diseases` (or --derive / --migrate-curated-compounds
/ --backfill-*) alone left every flag in the old `any([...])` guard False, so
main() flipped on --all and ran the entire load pipeline under a maintenance
command — which on kgdev hung the box and re-created data the migration had just
cleaned up. This pins that every maintenance flag counts as an explicit action.
"""
import argparse

import pytest

from database.load_knowledge_graph import (
    has_explicit_action,
    EXPLICIT_ACTION_FLAGS,
)


def _args(**flags):
    ns = argparse.Namespace(**{name: False for name in EXPLICIT_ACTION_FLAGS})
    for k, v in flags.items():
        setattr(ns, k, v)
    return ns


@pytest.mark.parametrize("flag", [
    "derive",
    "migrate_curated_compounds",
    "migrate_duplicate_diseases",
    "backfill_scfa",
    "backfill_disbiome_papers",
])
def test_standalone_maintenance_flag_is_an_explicit_action(flag):
    # so main() will NOT default to --all when only this flag is given
    assert has_explicit_action(_args(**{flag: True})) is True


def test_no_flags_defaults_to_all():
    assert has_explicit_action(_args()) is False


def test_a_load_flag_is_an_explicit_action():
    assert has_explicit_action(_args(hmdb=True)) is True


def test_missing_attr_does_not_re_arm_the_footgun():
    # A Namespace lacking a flag must read as off, not raise — otherwise a
    # partial Namespace could crash into the --all default.
    ns = argparse.Namespace(derive=True)  # only one attr present
    assert has_explicit_action(ns) is True
    assert has_explicit_action(argparse.Namespace()) is False
