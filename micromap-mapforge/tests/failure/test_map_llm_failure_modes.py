"""Failure modes the LLM `map` step has to survive cleanly (issue #62 scope).

The propose_mapping() function is a thin wrapper around an Anthropic client
call. These tests cover the things the issue lists as production failure
modes — and pin the contract that none of them produce a half-validated
mapping dict.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _profile():
    from micromap_mapforge.inspect.dispatch import inspect
    return inspect(FIXTURES / "study.tsv")


def _fake_anthropic_error(cls_name: str) -> Exception:
    """Construct an Anthropic SDK error without forcing test-time imports."""
    import anthropic
    cls = getattr(anthropic, cls_name)
    # The SDK errors take varying constructor args; use a minimal subclass that
    # bypasses the upstream signature. Tests only care about isinstance + the
    # message, not request/response wiring.
    return type(cls_name, (cls,), {"__init__": lambda self, msg="boom": Exception.__init__(self, msg)})()


def test_propose_mapping_propagates_api_timeout():
    """Production scenario: the LLM call takes longer than the SDK's timeout.

    The caller needs to see a clean exception (not a partially-built mapping)
    so the CLI layer can wrap it into a typed error.
    """
    import anthropic
    from micromap_mapforge.mapping.mapper import propose_mapping

    client = MagicMock()
    client.messages.create.side_effect = _fake_anthropic_error("APITimeoutError")

    with pytest.raises(anthropic.APITimeoutError):
        propose_mapping(_profile(), hint=None, client=client)


def test_propose_mapping_propagates_rate_limit():
    """Production scenario: the contributor's account is over its rate limit."""
    import anthropic
    from micromap_mapforge.mapping.mapper import propose_mapping

    client = MagicMock()
    client.messages.create.side_effect = _fake_anthropic_error("RateLimitError")

    with pytest.raises(anthropic.RateLimitError):
        propose_mapping(_profile(), hint=None, client=client)


def test_propose_mapping_propagates_connection_error():
    """Production scenario: network glitch, DNS failure, TLS handshake fail."""
    import anthropic
    from micromap_mapforge.mapping.mapper import propose_mapping

    client = MagicMock()
    client.messages.create.side_effect = _fake_anthropic_error("APIConnectionError")

    with pytest.raises(anthropic.APIConnectionError):
        propose_mapping(_profile(), hint=None, client=client)


def test_propose_mapping_rejects_empty_response_text():
    """Production scenario: the LLM returned content blocks with no text
    (extracted text is empty after the join+strip). YAML safe-load returns
    None, which is not a dict — must raise a typed mapping error so the CLI
    can surface it cleanly."""
    from micromap_mapforge.mapping.mapper import propose_mapping
    from micromap_mapforge.mapping.validator import MappingValidationError

    fake_message = MagicMock()
    fake_message.content = [MagicMock(text="")]
    client = MagicMock()
    client.messages.create.return_value = fake_message

    with pytest.raises(MappingValidationError):
        propose_mapping(_profile(), hint=None, client=client)


def test_propose_mapping_rejects_non_dict_yaml():
    """Production scenario: the LLM returns a YAML list (e.g. ['- bad', '- bad'])
    or a scalar instead of the expected mapping object. Must raise the typed
    mapping error."""
    from micromap_mapforge.mapping.mapper import propose_mapping
    from micromap_mapforge.mapping.validator import MappingValidationError

    fake_message = MagicMock()
    # Valid YAML, but the top-level type is a list — propose_mapping must reject.
    fake_message.content = [MagicMock(text="- not\n- a\n- mapping\n")]
    client = MagicMock()
    client.messages.create.return_value = fake_message

    with pytest.raises(MappingValidationError, match="not a YAML object"):
        propose_mapping(_profile(), hint=None, client=client)


def test_propose_mapping_rejects_content_block_without_text_attribute():
    """Production scenario: the SDK returns a tool-use block (no .text) and
    no text blocks at all — equivalent to an empty response. Must raise."""
    from micromap_mapforge.mapping.mapper import propose_mapping
    from micromap_mapforge.mapping.validator import MappingValidationError

    # MagicMock with text-attribute access removed via spec=[]
    tool_block = MagicMock(spec=[])  # no .text attribute
    fake_message = MagicMock()
    fake_message.content = [tool_block]
    client = MagicMock()
    client.messages.create.return_value = fake_message

    with pytest.raises(MappingValidationError):
        propose_mapping(_profile(), hint=None, client=client)
