"""The HMDB write Cypher must persist every field the loader computes.

Caught in production, not by tests: PR #278/#279 added `synonyms` and
`hmdb_status` to `_parse_metabolite_element` and `transform`, but
`_load_compounds` writes an EXPLICIT property list — so both values were
computed, passed into the batch, and silently dropped. After a full `--hmdb`
run on kgdev, all 2,247 INCHIKEY compounds had `synonyms = null` and
`hmdb_status = null`.

That is not cosmetic: #276's `_resolve_canonical_compound` matches curated
names against `m.synonyms` and ranks candidates on `m.hmdb_status`. With both
null, the resolver can never match a synonym — so 'Butyrate' still cannot reach
'Butyric acid' and the mechanistic hop stays severed. The fix looked complete
and was inert.

The existing suite could not catch this: it tests parse/transform output and the
resolver's ranking with mocks, but nothing asserted that the fields survive the
write. These tests pin the parse -> write contract itself.
"""
from unittest.mock import MagicMock

import pytest

from database.ingestion.hmdb_loader import HMDBLoader

# Fields the resolver in produces_loader (#276) reads off a :Compound.
RESOLVER_CRITICAL_FIELDS = ("synonyms", "hmdb_status")


@pytest.fixture
def loader():
    inst = HMDBLoader.__new__(HMDBLoader)
    inst.driver = MagicMock()
    inst.organization_id = "test-org"
    inst.batch_size = 100
    inst.stats = MagicMock()
    inst._calls = []

    def fake_execute(cypher, params=None, write=True):
        inst._calls.append((cypher, params or {}))
        return [{"count": 1}]

    inst.execute_cypher = fake_execute
    return inst


def _compound_batch():
    return [{
        "compound_id": "INCHIKEY:FERIUCNNQQJTOY-UHFFFAOYSA-N",
        "hmdb_id": "HMDB0000039",
        "name": "Butyric acid",
        "iupac_name": "butanoic acid",
        "synonyms": ["Butyrate", "Butanoate", "1-Butyric acid"],
        "hmdb_status": "quantified",
        "organization_id": "test-org",
        "is_scfa": True,
        "carbon_chain_length": 4,
    }]


class TestWritePersistsResolverFields:
    @pytest.mark.parametrize("field", RESOLVER_CRITICAL_FIELDS)
    def test_on_create_writes_field(self, loader, field):
        loader._load_compounds(_compound_batch())
        cypher = loader._calls[0][0]
        assert f"met.{field} = m.{field}" in cypher, (
            f"ON CREATE SET drops {field!r}: it is parsed and passed in the batch "
            f"but never written, so #276's resolver reads null"
        )

    @pytest.mark.parametrize("field", RESOLVER_CRITICAL_FIELDS)
    def test_on_match_backfills_field(self, loader, field):
        """Existing nodes must gain these on a re-run.

        `ON MATCH SET` only set `updated_at`, so the 1,598 compounds already
        loaded before #278 could never acquire synonyms — a re-run reported
        `nodes_updated: 0` and changed nothing.
        """
        loader._load_compounds(_compound_batch())
        cypher = loader._calls[0][0]
        on_match = cypher.split("ON MATCH SET", 1)
        assert len(on_match) == 2, "query must have an ON MATCH SET clause"
        assert f"met.{field} = m.{field}" in on_match[1], (
            f"ON MATCH SET does not backfill {field!r}; already-loaded compounds "
            f"stay null forever"
        )

    def test_batch_values_actually_reach_the_query(self, loader):
        """Guard the other half: the field must be in the params, not just the Cypher."""
        loader._load_compounds(_compound_batch())
        _, params = loader._calls[0]
        sent = params["compounds"][0]
        assert sent["synonyms"] == ["Butyrate", "Butanoate", "1-Butyric acid"]
        assert sent["hmdb_status"] == "quantified"

    def test_underscore_keys_are_still_stripped(self, loader):
        """_pathways/_diseases/_proteins are relationship payloads, not node props."""
        batch = _compound_batch()
        batch[0]["_pathways"] = [{"name": "x"}]
        loader._load_compounds(batch)
        _, params = loader._calls[0]
        assert "_pathways" not in params["compounds"][0]
