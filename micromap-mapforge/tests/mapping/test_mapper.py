from pathlib import Path

from micromap_mapforge.inspect.dispatch import inspect
from micromap_mapforge.mapping.mapper import draft_heuristic_mapping

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_heuristic_mapping_on_tsv_fixture():
    profile = inspect(FIXTURES / "study.tsv")
    mapping = draft_heuristic_mapping(profile)
    # Validates against schema (raises if not)
    from micromap_mapforge.mapping.validator import validate_mapping
    validate_mapping(mapping)

    labels = [e["label"] for e in mapping["entities"]]
    # 'tax_id' column → Taxon; 'condition' column → Disease (via common_columns_hint)
    assert "Taxon" in labels
    assert "Disease" in labels


def test_heuristic_mapping_preserves_source():
    profile = inspect(FIXTURES / "study.csv")
    mapping = draft_heuristic_mapping(profile)
    assert mapping["source"]["name"] == Path(profile.path).stem
    assert mapping["source"]["format"] == "csv"
    assert mapping["source"]["path"] == profile.path


def test_heuristic_confidence_is_inferred():
    profile = inspect(FIXTURES / "study.tsv")
    mapping = draft_heuristic_mapping(profile)
    # Heuristic guesses are never EXTRACTED — reviewer must confirm
    for entity in mapping["entities"]:
        assert entity["confidence"] == "INFERRED"


from unittest.mock import MagicMock


def test_propose_mapping_uses_llm_response():
    """propose_mapping should call the Anthropic client and return a validated mapping."""
    from micromap_mapforge.inspect.dispatch import inspect
    from micromap_mapforge.mapping.mapper import propose_mapping

    profile = inspect(FIXTURES / "study.tsv")

    fake_mapping_yaml = """
source:
  name: study
  format: tsv
  path: study.tsv
entities:
  - label: Taxon
    match_on: ncbi_tax_id
    columns:
      ncbi_tax_id: tax_id
      scientific_name: organism
    confidence: EXTRACTED
  - label: Disease
    match_on: name_normalized
    normalizer: normalize_disease_name
    columns:
      name: condition
    confidence: EXTRACTED
relationships: []
"""

    fake_client = MagicMock()
    fake_message = MagicMock()
    fake_message.content = [MagicMock(text=fake_mapping_yaml.strip())]
    fake_client.messages.create.return_value = fake_message

    mapping = propose_mapping(profile, hint=None, client=fake_client)

    labels = [e["label"] for e in mapping["entities"]]
    assert "Taxon" in labels
    assert "Disease" in labels
    # LLM-proposed entities can be EXTRACTED (LLM asserted high confidence)
    assert any(e["confidence"] == "EXTRACTED" for e in mapping["entities"])
    assert fake_client.messages.create.called


def test_propose_mapping_falls_back_on_invalid_yaml():
    """If the LLM returns something that fails schema validation, raise clearly."""
    from micromap_mapforge.inspect.dispatch import inspect
    from micromap_mapforge.mapping.mapper import propose_mapping
    from micromap_mapforge.mapping.validator import MappingValidationError

    profile = inspect(FIXTURES / "study.tsv")

    fake_client = MagicMock()
    fake_message = MagicMock()
    fake_message.content = [MagicMock(text="not: a valid: mapping: at all")]
    fake_client.messages.create.return_value = fake_message

    import pytest
    with pytest.raises(MappingValidationError):
        propose_mapping(profile, hint=None, client=fake_client)
