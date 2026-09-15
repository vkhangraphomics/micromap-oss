"""Deterministic validation of mapping.yaml against mapping.schema.json."""

import json
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

_SCHEMA_PATH = Path(__file__).parent / "mapping.schema.json"


class MappingValidationError(ValueError):
    """Raised when a mapping.yaml fails schema validation."""


def _load_schema() -> dict[str, Any]:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _format_error(e: ValidationError) -> str:
    path = ".".join(str(p) for p in e.absolute_path) if e.absolute_path else "(root)"
    return f"{path}: {e.message}"


def validate_mapping(mapping: dict[str, Any]) -> None:
    """Validate a mapping dict in-place. Raises MappingValidationError on failure.

    E3 (#78): the validator is constructed with `FormatChecker()` so the
    new source.url and source.contact fields actually enforce their
    `format: uri` / `format: email` declarations. Draft 2020-12's format
    vocabulary is advisory unless the validator is told to check.
    """
    validator = Draft202012Validator(_load_schema(), format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(mapping), key=lambda e: list(e.absolute_path))
    if errors:
        messages = [_format_error(e) for e in errors]
        raise MappingValidationError("mapping invalid:\n  - " + "\n  - ".join(messages))


def load_and_validate(path: str | Path) -> dict[str, Any]:
    """Load YAML from path, validate, return the parsed dict."""
    p = Path(path)
    mapping = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(mapping, dict):
        raise MappingValidationError(
            f"{p}: expected top-level object, got {type(mapping).__name__}"
        )
    validate_mapping(mapping)
    return mapping
