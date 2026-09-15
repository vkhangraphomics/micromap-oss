"""PrimeKG BioCypher adapter (#374).

Reads the two real Harvard Dataverse files directly via pyarrow — `path` is
expected to hold `nodes.tab` (tab-delimited: node_index, node_id, node_type,
node_name, node_source) and `edges.csv` (comma-delimited: relation,
display_relation, x_index, y_index) unmodified, as downloaded from
https://doi.org/10.7910/DVN/IXA7BM. `edges.csv` carries no endpoint types of
its own, so edges are resolved against a node_index -> (curie, label)
lookup built from nodes.tab.

Node labels map PrimeKG's own `node_type` onto this project's canonical
names where one exists (gene/protein->Gene, disease->Disease, drug->Drug,
pathway->Pathway); everything else is PascalCased from PrimeKG's own type
name. `exposure` nodes are CTD-sourced — the same commercial-use-restricted
source (verified during the OptimusKG evaluation for #374: "any
reproduction or use for commercial purpose is prohibited without prior
express written permission") that shows up as one of OptimusKG's integrated
sources too — so they're excluded by default (`exclude_node_types`).
Drug nodes key on DrugBank's *identifier* namespace only (e.g.
`DrugBank:DB09130`, a bare ID string) — not DrugBank's own restricted
content, same reasoning as DGIdb's `drugbank:` concept-id references.

Edge types pass through PrimeKG's own `relation` value (uppercased) rather
than being remapped onto this project's canonical relationship vocabulary:
faithful pass-through beats a hand-built ~29-entry mapping table that
couldn't be fully verified against live data this session. Edges
referencing an excluded node are dropped too, not left dangling for
import-kg's generated `MATCH` to silently miss.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Iterator

import pyarrow.csv as pa_csv


class MissingColumnsError(ValueError):
    """Raised when a PrimeKG file's schema lacks a required column."""


_NODE_COLUMNS = {"node_index", "node_id", "node_type", "node_name", "node_source"}
_EDGE_COLUMNS = {"relation", "display_relation", "x_index", "y_index"}

_NODE_TYPE_LABELS = {
    "gene/protein": "Gene",
    "disease": "Disease",
    "drug": "Drug",
    "pathway": "Pathway",
    "biological_process": "BiologicalProcess",
    "molecular_function": "MolecularFunction",
    "cellular_component": "CellularComponent",
    "anatomy": "Anatomy",
    "effect/phenotype": "Phenotype",
    "exposure": "Exposure",
}

DEFAULT_EXCLUDE_NODE_TYPES: frozenset[str] = frozenset({"exposure"})


class PrimeKGAdapter:
    """Adapter API contract: get_nodes() / get_edges() / schema_config / name."""

    name = "primekg"
    schema_config = {
        "name": "primekg",
        "prefixes": {
            "NCBI": "https://identifiers.org/ncbigene:",
            "MONDO": "https://identifiers.org/mondo:",
            "DrugBank": "https://identifiers.org/drugbank:",
            "REACTOME": "https://identifiers.org/reactome:",
        },
    }

    def __init__(
        self,
        path: str,
        exclude_node_types: frozenset[str] = DEFAULT_EXCLUDE_NODE_TYPES,
    ):
        self.datasets_dir = Path(path)
        self.exclude_node_types = exclude_node_types
        self._node_index: dict[int, tuple[str, str]] | None = None  # node_index -> (curie, label)

    def get_nodes(self) -> Iterator[tuple]:
        for _node_index, curie, label, name, node_source in self._iter_node_rows():
            yield (
                curie,
                label,
                {"name": name},
                {"source": "primekg", "node_source": node_source},
                "EXTRACTED",
                None,
            )

    def get_edges(self) -> Iterator[tuple]:
        node_index = self._load_node_index()
        for row in self._read_edges():
            endpoints = node_index.get(row["x_index"]), node_index.get(row["y_index"])
            if endpoints[0] is None or endpoints[1] is None:
                continue  # one endpoint is an excluded node type
            from_curie, _from_label = endpoints[0]
            to_curie, _to_label = endpoints[1]
            yield (
                row["relation"].upper(),
                from_curie,
                to_curie,
                {"display_relation": row["display_relation"]},
                {"source": "primekg"},
                "EXTRACTED",
                None,
            )

    # -- node index (shared by get_nodes() and get_edges(), no call-order dependency) --

    def _load_node_index(self) -> dict[int, tuple[str, str]]:
        if self._node_index is None:
            self._node_index = {
                idx: (curie, label)
                for idx, curie, label, _name, _node_source in self._iter_node_rows()
            }
        return self._node_index

    def _iter_node_rows(self) -> Iterable[tuple[int, str, str, str, str]]:
        for row in self._read_nodes():
            node_type = row["node_type"]
            if node_type in self.exclude_node_types:
                continue
            label = _NODE_TYPE_LABELS.get(node_type, _pascal_case(node_type))
            curie = f"{row['node_source']}:{row['node_id']}"
            yield row["node_index"], curie, label, row["node_name"], row["node_source"]

    # -- file reading -----------------------------------------------------

    def _read_nodes(self):
        path = self.datasets_dir / "nodes.tab"
        if not path.is_file():
            raise FileNotFoundError(f"PrimeKG nodes file not found: {path}")
        reader = pa_csv.open_csv(path, parse_options=pa_csv.ParseOptions(delimiter="\t"))
        missing = _NODE_COLUMNS - set(reader.schema.names)
        if missing:
            raise MissingColumnsError(
                f"nodes.tab schema is missing required column(s): {sorted(missing)} "
                f"(found: {sorted(reader.schema.names)})"
            )
        for batch in reader:
            yield from batch.to_pylist()

    def _read_edges(self):
        path = self.datasets_dir / "edges.csv"
        if not path.is_file():
            raise FileNotFoundError(f"PrimeKG edges file not found: {path}")
        reader = pa_csv.open_csv(path)
        missing = _EDGE_COLUMNS - set(reader.schema.names)
        if missing:
            raise MissingColumnsError(
                f"edges.csv schema is missing required column(s): {sorted(missing)} "
                f"(found: {sorted(reader.schema.names)})"
            )
        for batch in reader:
            yield from batch.to_pylist()


def _pascal_case(snake: str) -> str:
    return "".join(part.capitalize() for part in snake.replace("/", "_").split("_"))
