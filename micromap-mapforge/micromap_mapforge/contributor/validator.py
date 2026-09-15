"""Validation for contributor.yaml — the inputs to routing policy."""

import json
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

_SCHEMA_PATH = Path(__file__).parent / "schema.json"


class ContributorValidationError(ValueError):
    """Raised when a contributor.yaml fails schema validation."""


def _load_schema() -> dict[str, Any]:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_contributor(doc: dict[str, Any]) -> None:
    validator = Draft202012Validator(_load_schema())
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
    if errors:
        msgs = [_format_error(e) for e in errors]
        raise ContributorValidationError("contributor invalid:\n  - " + "\n  - ".join(msgs))


def load_contributor(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ContributorValidationError(
            f"{p}: expected top-level object, got {type(doc).__name__}"
        )
    validate_contributor(doc)
    return doc


def _format_error(e: ValidationError) -> str:
    path = ".".join(str(p) for p in e.absolute_path) if e.absolute_path else "(root)"
    return f"{path}: {e.message}"
