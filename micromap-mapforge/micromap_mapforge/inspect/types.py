"""Shared types produced by the Inspect pass."""

from dataclasses import dataclass, asdict, field
from typing import Any, Literal


InferredType = Literal[
    "integer", "float", "boolean", "string",
    "date", "datetime", "json", "unknown",
]

# #286 G7: SourceFormat is an OPEN string, not a closed Literal. A new inspector
# registers its format via register_format() instead of editing this line — the
# same closed-enum coupling #202 retired for resolver/capability selection. Kept
# as a named alias so annotations still read as `SourceFormat`.
SourceFormat = str

#: Registry of known source-format names, for introspection + "accepted formats"
#: reporting. Seeded with the built-in inspectors below; new inspectors add to it
#: via register_format(). Membership is not enforced on SourceProfile (an unknown
#: format is a bug in an inspector, not caller input) — it exists to enumerate.
KNOWN_FORMATS: set[str] = set()


def register_format(name: str) -> str:
    """Register a source-format name and return it, so an inspector can write
    ``FORMAT = register_format("vcf")``. Idempotent."""
    KNOWN_FORMATS.add(name)
    return name


for _builtin in ("csv", "tsv", "json", "jsonl", "parquet", "sql_dump", "xlsx", "pdf"):
    register_format(_builtin)


@dataclass
class ColumnProfile:
    name: str
    inferred_type: InferredType
    null_rate: float             # 0.0 - 1.0
    distinct_count: int          # estimate; -1 if not computed
    samples: list[Any]           # up to N sample values
    structured: dict[str, Any] | None = None  # D3: one-column-many-fields hint

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SourceProfile:
    path: str
    format: SourceFormat
    row_count_estimate: int      # -1 if unknown
    columns: list[ColumnProfile]
    samples: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""               # inspector-level hint, e.g. multi-sheet workbook
    #: Structured inspector-level metadata that isn't a column surface. Used by
    #: the #286 G8b raw provenance stub (size_bytes/sha256/read_count/kind).
    #: None for the column-bearing inspectors.
    meta: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
