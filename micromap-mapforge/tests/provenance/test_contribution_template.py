from datetime import datetime, timezone

from micromap_mapforge.provenance.contribution import (
    ContributionRecord,
    cypher_for_contribution,
)


def _record():
    return ContributionRecord(
        contributor="partner-xyz",
        reviewer="alice",
        organization_id="partner-xyz",
        source_sha256="abc123",
        mapping_sha256="def456",
        destination="micromap-core",
        submitted_at=datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc),
        resolved_count=42,
        unresolved_count=3,
        ambiguous_count=1,
    )


def test_record_fields():
    r = _record()
    assert r.contributor == "partner-xyz"
    assert r.resolved_count == 42


def test_cypher_returns_5_statements_with_params():
    stmts = cypher_for_contribution(_record())
    assert len(stmts) == 5
    for stmt, params in stmts:
        assert isinstance(stmt, str)
        assert isinstance(params, dict)
        # Values must NEVER be interpolated — only $params appear in the Cypher
        assert "def456" not in stmt   # no mapping_sha256 in string
        assert "abc123" not in stmt   # no source_sha256 in string
        assert "'partner-xyz'" not in stmt
        assert "'alice'" not in stmt


def test_cypher_structure():
    stmts = cypher_for_contribution(_record())
    joined = "\n".join(s for s, _ in stmts)
    assert "MERGE (org:Organization" in joined
    assert "MERGE (rev:Reviewer" in joined
    assert "MERGE (c:Contribution" in joined
    assert "CONTRIBUTED" in joined
    assert "APPROVED_BY" in joined


def test_cypher_params_contain_all_values():
    stmts = cypher_for_contribution(_record())
    # Collect all param values across all statements
    all_values = set()
    for _, params in stmts:
        for v in params.values():
            all_values.add(str(v))
    assert "partner-xyz" in all_values
    assert "alice" in all_values
    assert "def456" in all_values
    assert "abc123" in all_values
    assert "42" in all_values                # resolved_count
    assert "micromap-core" in all_values     # destination


def test_cypher_injection_attempt_does_not_produce_extra_statements():
    """Sanity check: a malicious reviewer name stays a single param value."""
    rec = ContributionRecord(
        contributor="attacker", reviewer="alice'; DETACH DELETE n; //",
        organization_id="attacker", source_sha256="x", mapping_sha256="y",
        destination="registry-only",
        submitted_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
        resolved_count=0, unresolved_count=0, ambiguous_count=0,
    )
    stmts = cypher_for_contribution(rec)
    # Still 5 statements, not 6+
    assert len(stmts) == 5
    # The malicious string appears ONLY in params, never in the query string
    for stmt, params in stmts:
        assert "DETACH DELETE" not in stmt


# ---------------------------------------------------------------------------
# E3 (#78): source attribution fields on :Contribution
# ---------------------------------------------------------------------------


def _e3_record(**overrides):
    """Like _record() but with all 8 e3 source-attribution kwargs populated."""
    base = dict(
        contributor="partner-xyz",
        reviewer="alice",
        organization_id="partner-xyz",
        source_sha256="abc123",
        mapping_sha256="def456",
        destination="micromap-core",
        submitted_at=datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc),
        resolved_count=42,
        unresolved_count=3,
        ambiguous_count=1,
        # E3 fields populated:
        source_name="reactome",
        source_license="CC-BY-4.0",
        source_url="https://reactome.org/download",
        source_doi="10.1093/nar/gkx1132",
        source_contact="help@reactome.org",
        source_version="v89",
        source_accessed_at="2026-06-06",
        source_ethics_ref="IRB-2024-0428",
    )
    base.update(overrides)
    return ContributionRecord(**base)


def test_contribution_record_accepts_all_e3_fields_as_optional():
    """Constructing with all 8 e3 kwargs stores them on the dataclass."""
    rec = _e3_record()
    assert rec.source_name == "reactome"
    assert rec.source_license == "CC-BY-4.0"
    assert rec.source_url == "https://reactome.org/download"
    assert rec.source_doi == "10.1093/nar/gkx1132"
    assert rec.source_contact == "help@reactome.org"
    assert rec.source_version == "v89"
    assert rec.source_accessed_at == "2026-06-06"
    assert rec.source_ethics_ref == "IRB-2024-0428"


def test_contribution_record_defaults_e3_fields_to_none():
    """Constructing WITHOUT the e3 kwargs leaves all 8 None.

    Pre-E3 callers construct ContributionRecord with only the 10 required
    fields. This test pins backward compatibility.
    """
    rec = _record()  # pre-E3 factory, no e3 kwargs
    assert rec.source_name is None
    assert rec.source_license is None
    assert rec.source_url is None
    assert rec.source_doi is None
    assert rec.source_contact is None
    assert rec.source_version is None
    assert rec.source_accessed_at is None
    assert rec.source_ethics_ref is None


def test_cypher_writes_e3_properties_when_present():
    """An e3-populated record produces a Contribution SET map with the e3
    keys; the params dict contains the e3 values."""
    stmts = cypher_for_contribution(_e3_record())
    # Find the Contribution MERGE statement (the one that mentions :Contribution).
    contrib_stmt, contrib_params = next(
        (s, p) for s, p in stmts if "MERGE (c:Contribution" in s
    )
    assert "source_license: $source_license" in contrib_stmt
    assert "source_doi: $source_doi" in contrib_stmt
    assert "source_accessed_at: $source_accessed_at" in contrib_stmt
    assert contrib_params["source_license"] == "CC-BY-4.0"
    assert contrib_params["source_doi"] == "10.1093/nar/gkx1132"
    assert contrib_params["source_accessed_at"] == "2026-06-06"


def test_cypher_omits_e3_properties_when_none():
    """A pre-E3 record (no e3 kwargs) produces Cypher with NO e3 keys in
    the SET map or the params dict. Pins the None-stripping contract."""
    stmts = cypher_for_contribution(_record())  # pre-E3 factory
    contrib_stmt, contrib_params = next(
        (s, p) for s, p in stmts if "MERGE (c:Contribution" in s
    )
    # None of the 8 e3 field names appears in the Cypher string.
    for field in (
        "source_name", "source_license", "source_url", "source_doi",
        "source_contact", "source_version", "source_accessed_at",
        "source_ethics_ref",
    ):
        assert field not in contrib_stmt, (
            f"Cypher leaked e3 field {field!r} when record didn't set it"
        )
        assert field not in contrib_params, (
            f"params dict leaked e3 key {field!r} when record didn't set it"
        )


def test_cypher_merge_key_unchanged_post_e3():
    """The Contribution MERGE key is still {mapping_sha256, source_sha256,
    organization_id} -- e3 fields are SET-only, not MERGE-key."""
    stmts = cypher_for_contribution(_e3_record())
    contrib_stmt = next(s for s, _ in stmts if "MERGE (c:Contribution" in s)
    # MERGE key contains exactly these three fields.
    merge_key_block = contrib_stmt.split("SET")[0]
    assert "mapping_sha256: $mapping_sha256" in merge_key_block
    assert "source_sha256: $source_sha256" in merge_key_block
    assert "organization_id: $organization_id" in merge_key_block
    # No e3 field appears in the MERGE key block.
    for field in ("source_license", "source_doi", "source_version"):
        assert field not in merge_key_block, (
            f"e3 field {field!r} leaked into MERGE key"
        )


def test_cypher_resubmission_overwrites_e3_fields():
    """Two records with identical MERGE-key fields but different licenses
    each produce Cypher with their own license value in params.

    Pins the latest-wins semantics. Submit produces independent SET
    operations per record; the MERGE collapses them to the same node.
    """
    rec_v1 = _e3_record(source_license="CC-BY-4.0")
    rec_v2 = _e3_record(source_license="CC-BY-SA-4.0")
    stmts_v1 = cypher_for_contribution(rec_v1)
    stmts_v2 = cypher_for_contribution(rec_v2)
    params_v1 = next(p for s, p in stmts_v1 if "MERGE (c:Contribution" in s)
    params_v2 = next(p for s, p in stmts_v2 if "MERGE (c:Contribution" in s)
    assert params_v1["source_license"] == "CC-BY-4.0"
    assert params_v2["source_license"] == "CC-BY-SA-4.0"
    # MERGE-key fields identical (same Contribution node on the server).
    assert params_v1["mapping_sha256"] == params_v2["mapping_sha256"]
    assert params_v1["source_sha256"] == params_v2["source_sha256"]
    assert params_v1["organization_id"] == params_v2["organization_id"]


# ---------------------------------------------------------------------------
# E4 (#78): force_submitted governance stamp on :Contribution
# ---------------------------------------------------------------------------


def test_cypher_writes_force_submitted_when_true():
    """A record with force_submitted=True produces a Contribution SET map
    with the field; the params dict contains the True value."""
    rec = ContributionRecord(
        contributor="acme", reviewer="alice", organization_id="acme",
        source_sha256="a1", mapping_sha256="b2",
        destination="micromap-core",
        submitted_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
        resolved_count=5, unresolved_count=0, ambiguous_count=0,
        force_submitted=True,
    )
    stmts = cypher_for_contribution(rec)
    contrib_stmt, contrib_params = next(
        (s, p) for s, p in stmts if "MERGE (c:Contribution" in s
    )
    assert "force_submitted: $force_submitted" in contrib_stmt
    assert contrib_params["force_submitted"] is True


def test_cypher_omits_force_submitted_when_none():
    """A record without force_submitted (default None) produces Cypher
    with NO force_submitted key in SET clause or params dict.

    Pins backward compat: pre-E4 records produce byte-identical Cypher.
    """
    rec = ContributionRecord(
        contributor="acme", reviewer="alice", organization_id="acme",
        source_sha256="a1", mapping_sha256="b2",
        destination="micromap-core",
        submitted_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
        resolved_count=5, unresolved_count=0, ambiguous_count=0,
        # force_submitted omitted — defaults to None
    )
    stmts = cypher_for_contribution(rec)
    contrib_stmt, contrib_params = next(
        (s, p) for s, p in stmts if "MERGE (c:Contribution" in s
    )
    assert "force_submitted" not in contrib_stmt, (
        "Cypher leaked force_submitted when record didn't set it"
    )
    assert "force_submitted" not in contrib_params, (
        "params dict leaked force_submitted when record didn't set it"
    )


def test_force_submitted_in_governance_allowlist_not_attribution():
    """Sanity check: force_submitted is in _GOVERNANCE_FIELDS, NOT in
    _SOURCE_ATTRIBUTION_FIELDS.

    Catches a future refactor that accidentally merges the two tuples
    (would still work behaviorally, but obscure the semantic split).
    """
    from micromap_mapforge.provenance.contribution import (
        _GOVERNANCE_FIELDS,
        _SOURCE_ATTRIBUTION_FIELDS,
    )
    assert "force_submitted" in _GOVERNANCE_FIELDS
    assert "force_submitted" not in _SOURCE_ATTRIBUTION_FIELDS
