"""#115: ``mapforge --help`` must render even when stdout's codec can't encode
Unicode used in subcommand help text.

The fix lives at module-import time in ``micromap_mapforge.cli`` — it detects a
limited stream encoding (e.g., Windows cp1252) and reconfigures the stream to
UTF-8 with ``errors='replace'``. We exercise the same scenario here via a
subprocess that forces stdout to cp1252 *before* importing the CLI, so the
import-time hook is what makes ``--help`` work. This runs on any host (CI is
Linux); the subprocess simulates the Windows environment.
"""

import subprocess
import sys


def test_help_does_not_crash_on_cp1252_stdout():
    """End-to-end repro for #115."""
    child = r"""
import sys, io
sys.stdout = io.TextIOWrapper(
    sys.stdout.buffer, encoding='cp1252', errors='strict', line_buffering=True
)
sys.stderr = io.TextIOWrapper(
    sys.stderr.buffer, encoding='cp1252', errors='strict', line_buffering=True
)
from micromap_mapforge.cli import main
try:
    main(['--help'])
except SystemExit as e:
    sys.exit(e.code or 0)
"""
    result = subprocess.run(
        [sys.executable, "-c", child],
        capture_output=True,
        timeout=30,
    )
    stderr_text = result.stderr.decode("utf-8", errors="replace")
    stdout_text = result.stdout.decode("utf-8", errors="replace")
    assert "UnicodeEncodeError" not in stderr_text, (
        f"--help still crashes with UnicodeEncodeError under cp1252 stdout:\n"
        f"{stderr_text}"
    )
    assert result.returncode == 0, (
        f"--help exited {result.returncode}; stderr={stderr_text!r}; "
        f"stdout={stdout_text!r}"
    )
    # The arrow IS in the import-kg subcommand summary — confirm help rendered
    # far enough to include it (proves the reconfigure landed before output).
    assert "import-kg" in stdout_text, (
        f"expected 'import-kg' in help output; got: {stdout_text!r}"
    )


def test_configure_streams_is_idempotent():
    """The helper must be safe to call multiple times — module re-import in
    test runs, or any future explicit invocation, should not error.
    """
    from micromap_mapforge.cli import _configure_streams_for_unicode

    _configure_streams_for_unicode()
    _configure_streams_for_unicode()
    _configure_streams_for_unicode()


def test_configure_streams_tolerates_no_encoding_attribute():
    """Some test/subprocess wrappers expose streams without an ``encoding``
    attribute. The helper must not raise.
    """
    from unittest.mock import patch

    from micromap_mapforge.cli import _configure_streams_for_unicode

    class FakeStream:
        pass

    fake = FakeStream()
    with patch.object(sys, "stdout", fake), patch.object(sys, "stderr", fake):
        _configure_streams_for_unicode()  # must not raise


def test_configure_streams_tolerates_no_reconfigure_method():
    """A stream with an ASCII-only encoding but no ``reconfigure`` method (some
    captured-stream contexts) must not crash the helper.
    """
    from unittest.mock import patch

    from micromap_mapforge.cli import _configure_streams_for_unicode

    class FakeStream:
        encoding = "ascii"
        # No reconfigure() method.

    with patch.object(sys, "stdout", FakeStream()), patch.object(sys, "stderr", FakeStream()):
        _configure_streams_for_unicode()  # must not raise
