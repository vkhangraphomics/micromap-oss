"""#152/#153 foundation: version discipline for the built-in templates.

The A4 semver/compat machinery (#151) only enforces MAJOR compatibility at load;
nothing stopped a template's content from changing while its ``version:`` stayed
put (the #286 hint additions did exactly that). Without a version signal, a diff
tool (#152) or a migration framework (#153) has no artifact to point at.

This pins each template to a committed lock of ``{version, body-sha256}`` and
enforces the rule that makes future versions meaningful: **a change to a
template's body must be accompanied by a version bump.** The lock's regenerator
refuses to record a body change under an unchanged version, so the only way to
get CI green after editing a template is to bump it.
"""
import pytest

from micromap_mapforge.mapping import template_lock as tl


def test_every_template_matches_the_committed_lock():
    """The CI guard: the live templates must equal the committed lock. A drift
    means a template changed without the lock (and thus the version) being
    updated — regenerate with `python -m micromap_mapforge.mapping.template_lock`."""
    current = tl.current_states()
    lock = tl.load_lock()
    assert current == lock, (
        "template(s) drifted from versions.lock — bump the changed template's "
        "`version:` and regenerate the lock:\n  "
        + "\n  ".join(f"{n}: live={current.get(n)} lock={lock.get(n)}"
                      for n in sorted(set(current) | set(lock)) if current.get(n) != lock.get(n))
    )


def test_lock_covers_every_template_file():
    """A newly added template must be locked too — no silent gaps."""
    assert set(tl.current_states()) == set(tl.load_lock())


def test_build_lock_refuses_body_change_without_version_bump():
    existing = {"microbiome": {"version": "1.0.0", "sha256": "AAA"}}
    changed = {"microbiome": {"version": "1.0.0", "sha256": "BBB"}}  # body changed, ver same
    with pytest.raises(tl.LockDisciplineError, match="(?i)microbiome"):
        tl.build_lock(changed, existing)


def test_build_lock_allows_body_change_with_version_bump():
    existing = {"microbiome": {"version": "1.0.0", "sha256": "AAA"}}
    bumped = {"microbiome": {"version": "1.1.0", "sha256": "BBB"}}
    assert tl.build_lock(bumped, existing) == bumped


def test_build_lock_allows_unchanged_and_new_templates():
    existing = {"microbiome": {"version": "1.0.0", "sha256": "AAA"}}
    current = {
        "microbiome": {"version": "1.0.0", "sha256": "AAA"},   # unchanged
        "brandnew": {"version": "1.0.0", "sha256": "CCC"},     # never locked before
    }
    assert tl.build_lock(current, existing) == current


def test_build_lock_reports_every_offender_at_once():
    existing = {
        "a": {"version": "1.0.0", "sha256": "1"},
        "b": {"version": "2.0.0", "sha256": "2"},
    }
    current = {
        "a": {"version": "1.0.0", "sha256": "1x"},   # changed, not bumped
        "b": {"version": "2.0.0", "sha256": "2x"},   # changed, not bumped
    }
    with pytest.raises(tl.LockDisciplineError) as ei:
        tl.build_lock(current, existing)
    assert "a" in str(ei.value) and "b" in str(ei.value)
