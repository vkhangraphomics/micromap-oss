"""C7 (adapt mode, #76): remap an adapter's raw labels onto a project's
ontology classes via BioCypher's `input_label` schema_config convention.

`adopt` mode (the default, existing behavior) takes an adapter's own
schema_config/labels verbatim. `adapt` overlays a *project* schema_config
instead — the customer's classes declare which raw adapter label(s) they
correspond to via `input_label` (BioCypher's own convention: a string or list
of strings), and this module builds the reverse lookup and applies it to an
OfflineAdapter snapshot before it becomes a ContributionBundle.
"""
import pytest

from micromap_mapforge.integration.biocypher.runner import OfflineAdapter
from micromap_mapforge.integration.biocypher.schema_adapt import (
    UnmappedLabelError,
    build_input_label_map,
    remap_offline_adapter,
)


def test_build_input_label_map_from_single_string():
    schema_config = {"classes": {"Taxon": {"input_label": "OrganismTaxon"}}}
    assert build_input_label_map(schema_config) == {"OrganismTaxon": "Taxon"}


def test_build_input_label_map_from_list():
    schema_config = {"classes": {"Compound": {"input_label": ["chemical", "small molecule"]}}}
    result = build_input_label_map(schema_config)
    assert result == {"chemical": "Compound", "small molecule": "Compound"}


def test_build_input_label_map_skips_classes_without_input_label():
    schema_config = {"classes": {"Taxon": {"slots": ["id"]}}}
    assert build_input_label_map(schema_config) == {}


def test_build_input_label_map_rejects_ambiguous_claim():
    schema_config = {
        "classes": {
            "Taxon": {"input_label": "thing"},
            "Compound": {"input_label": "thing"},
        }
    }
    with pytest.raises(ValueError, match="thing"):
        build_input_label_map(schema_config)


def _offline(nodes=(), edges=()):
    return OfflineAdapter(
        name="fake", schema_config={"name": "adapter-own"},
        nodes=list(nodes), edges=list(edges),
    )


def test_remap_offline_adapter_remaps_node_labels():
    offline = _offline(nodes=[("id:1", "OrganismTaxon", {}, {}, None, None)])
    project = {"classes": {"Taxon": {"input_label": "OrganismTaxon"}}}

    result = remap_offline_adapter(offline, project)

    assert result.nodes[0][1] == "Taxon"


def test_remap_offline_adapter_remaps_edge_types():
    offline = _offline(edges=[("MEMBER_OF", "id:1", "id:2", {}, {}, None, None)])
    project = {"classes": {"HAS_PARENT": {"input_label": "MEMBER_OF"}}}

    result = remap_offline_adapter(offline, project)

    assert result.edges[0][0] == "HAS_PARENT"


def test_remap_offline_adapter_uses_project_schema_config():
    offline = _offline()
    project = {"name": "my-project", "classes": {}}

    result = remap_offline_adapter(offline, project)

    assert result.schema_config is project


def test_remap_offline_adapter_raises_on_unmapped_label():
    offline = _offline(nodes=[("id:1", "Unmapped", {}, {}, None, None)])
    project = {"classes": {"Taxon": {"input_label": "OrganismTaxon"}}}

    with pytest.raises(UnmappedLabelError, match="Unmapped"):
        remap_offline_adapter(offline, project)
