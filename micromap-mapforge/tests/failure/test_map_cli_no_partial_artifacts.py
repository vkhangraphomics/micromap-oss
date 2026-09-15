"""When the LLM `map` step fails, the CLI must:

1. Exit with a non-zero status (so any wrapping script knows it failed).
2. Print a clear, single-line error message (no Python stack trace).
3. NOT leave a half-written `mapping.yaml` in the output directory.

Issue #62 calls these out explicitly under "map step" failure modes.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from micromap_mapforge.cli import main


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _fake_anthropic_error(cls_name: str) -> Exception:
    """Construct an Anthropic SDK error usable as a side_effect."""
    import anthropic
    cls = getattr(anthropic, cls_name)
    return type(cls_name, (cls,), {
        "__init__": lambda self, msg=f"simulated {cls_name}": Exception.__init__(self, msg)
    })()


def _invoke_map_with_failing_llm(tmp_path: Path, error_cls: str):
    """Run `mapforge map --mode llm` with a client that raises ``error_cls``.

    Returns (result, out_dir). Caller asserts on result.exit_code,
    result.output, and whether out_dir / 'mapping.yaml' was created.
    """
    out = tmp_path / "out"

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = _fake_anthropic_error(error_cls)

    runner = CliRunner()
    with patch(
        "micromap_mapforge.cli._build_anthropic_client",
        return_value=fake_client,
    ):
        result = runner.invoke(main, [
            "map", str(FIXTURES / "study.tsv"),
            "--out", str(out),
            "--mode", "llm",
        ])
    return result, out


def test_map_cli_on_llm_timeout_exits_cleanly(tmp_path):
    result, _out = _invoke_map_with_failing_llm(tmp_path, "APITimeoutError")
    assert result.exit_code != 0
    assert "Traceback" not in result.output, (
        "CLI leaked a Python traceback on LLM timeout — should emit a "
        "single-line clean error message instead. result.output:\n"
        f"{result.output}"
    )
    # The output must mention "timeout" so an operator can act.
    assert "timeout" in result.output.lower()


def test_map_cli_on_rate_limit_exits_cleanly(tmp_path):
    result, _out = _invoke_map_with_failing_llm(tmp_path, "RateLimitError")
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "rate limit" in result.output.lower() or "ratelimit" in result.output.lower()


def test_map_cli_on_connection_error_exits_cleanly(tmp_path):
    result, _out = _invoke_map_with_failing_llm(tmp_path, "APIConnectionError")
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    # APIConnectionError covers DNS, TCP, TLS — output should hint at network.
    output = result.output.lower()
    assert any(token in output for token in ("connect", "network", "unreachable"))


def test_map_cli_failed_llm_leaves_no_mapping_yaml(tmp_path):
    """The critical no-half-artifacts invariant for all transport failures."""
    for error_cls in ("APITimeoutError", "RateLimitError", "APIConnectionError"):
        bundle_subdir = tmp_path / error_cls.lower()
        result, out = _invoke_map_with_failing_llm(bundle_subdir, error_cls)
        assert result.exit_code != 0, (
            f"{error_cls}: expected non-zero exit, got {result.exit_code}\n"
            f"{result.output}"
        )
        # No mapping.yaml written. (out_dir itself may or may not exist —
        # the contract is about the artifact, not the parent directory.)
        assert not (out / "mapping.yaml").exists(), (
            f"{error_cls}: left a partial mapping.yaml in {out} after the "
            f"LLM call failed."
        )
