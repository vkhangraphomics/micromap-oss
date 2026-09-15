"""Graph invariants, defined once (#298).

`tests/test_kg_integrity.py` asserts these against whatever graph `NEO4J_URI`
points at, and skips entirely when that box is unreachable — which is always, in
CI. `tests/test_kg_invariants_seeded.py` runs the SAME queries against an
ephemeral seeded Neo4j so they are actually exercised.

Sharing the text matters more than it looks. If the seeded suite held its own
copy of each query, it would prove that *the copy* works while the live suite
kept running something else — which is exactly how #298 happened: three
guardrails queried `CHILD_OF`, a type nothing writes, and passed unconditionally
because nothing ever checked that the query could fail. Import from here; do not
re-type the Cypher.

Every invariant carries a `violation_setup` that creates exactly one violation.
That is not a convenience: a check nobody has ever seen fail is indistinguishable
from a check that cannot fail, so the seeded suite asserts each query catches its
own violation. An invariant without a violation case is rejected by a test in
that module, which makes an unprovable check impossible to add.
"""
from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class Invariant:
    """One graph invariant: a counting query plus a way to break it."""

    name: str
    #: Cypher returning a single `violations` column; 0 means the invariant holds.
    cypher: str
    #: Cypher creating exactly one violation of `cypher`, for the seeded suite.
    violation_setup: str
    #: Why this matters, quoted in the failure message.
    description: str

    def count(self, session) -> int:
        record = session.run(self.cypher).single()
        return record["violations"] if record else 0


INVARIANTS: Tuple[Invariant, ...] = (
    Invariant(
        name="no_self_referencing_has_parent",
        cypher="""
            MATCH (t:Taxon)-[r:HAS_PARENT]->(t)
            RETURN count(r) AS violations
        """,
        violation_setup="""
            CREATE (t:Taxon {name: 'selfref', organization_id: 'test'})-[:HAS_PARENT]->(t)
        """,
        description="a taxon that is its own parent breaks every ancestor walk",
    ),
    Invariant(
        name="taxonomy_no_short_cycles",
        cypher="""
            MATCH path = (t:Taxon)-[:HAS_PARENT*2..5]->(t)
            RETURN count(path) AS violations
        """,
        violation_setup="""
            CREATE (a:Taxon {name: 'cyc-a', organization_id: 'test'})
            CREATE (b:Taxon {name: 'cyc-b', organization_id: 'test'})
            CREATE (c:Taxon {name: 'cyc-c', organization_id: 'test'})
            CREATE (a)-[:HAS_PARENT]->(b)
            CREATE (b)-[:HAS_PARENT]->(c)
            CREATE (c)-[:HAS_PARENT]->(a)
        """,
        description="a cycle in the hierarchy makes lineage queries non-terminating",
    ),
    Invariant(
        name="taxa_have_a_name",
        cypher="""
            MATCH (t:Taxon)
            WHERE t.name IS NULL OR trim(t.name) = ''
            RETURN count(t) AS violations
        """,
        violation_setup="""
            CREATE (:Taxon {taxon_id: 'NCBITaxon:noname', organization_id: 'test'})
        """,
        description="a nameless taxon cannot be resolved or displayed",
    ),
    Invariant(
        name="diseases_have_a_name",
        cypher="""
            MATCH (d:Disease)
            WHERE d.name IS NULL OR trim(d.name) = ''
            RETURN count(d) AS violations
        """,
        violation_setup="""
            CREATE (:Disease {disease_id: 'noname', organization_id: 'test'})
        """,
        description="a nameless disease cannot be matched across sources",
    ),
    Invariant(
        name="associated_with_disease_has_direction",
        cypher="""
            MATCH ()-[r:ASSOCIATED_WITH_DISEASE]->()
            WHERE r.direction IS NULL
            RETURN count(r) AS violations
        """,
        violation_setup="""
            CREATE (t:Taxon {name: 'nodir-taxon', organization_id: 'test'})
            CREATE (d:Disease {name: 'nodir-disease', organization_id: 'test'})
            CREATE (t)-[:ASSOCIATED_WITH_DISEASE]->(d)
        """,
        description=(
            "direction (up/down) is the whole content of an association; without "
            "it the edge asserts only that someone looked"
        ),
    ),
    Invariant(
        name="has_parent_endpoints_are_taxa",
        cypher="""
            MATCH (a)-[r:HAS_PARENT]->(b)
            WHERE NOT (a:Taxon) OR NOT (b:Taxon)
            RETURN count(r) AS violations
        """,
        violation_setup="""
            CREATE (t:Taxon {name: 'bad-child', organization_id: 'test'})
            CREATE (d:Disease {name: 'not-a-taxon', organization_id: 'test'})
            CREATE (t)-[:HAS_PARENT]->(d)
        """,
        description=(
            "the hierarchy must connect taxa to taxa; this is the check that "
            "#298 disarmed by spelling the relationship CHILD_OF"
        ),
    ),
    Invariant(
        name="no_orphaned_diseases",
        cypher="""
            MATCH (d:Disease)
            WHERE NOT (d)--()
            RETURN count(d) AS violations
        """,
        violation_setup="""
            CREATE (:Disease {name: 'orphan-disease', organization_id: 'test'})
        """,
        description="a disease with no edges is unreachable from any query",
    ),
    Invariant(
        name="no_orphaned_compounds",
        cypher="""
            MATCH (c:Compound)
            WHERE NOT (c)--()
            RETURN count(c) AS violations
        """,
        violation_setup="""
            CREATE (:Compound {name: 'orphan-compound', organization_id: 'test'})
        """,
        description=(
            "an edgeless compound is invisible to every traversal — 1,941 of "
            "these were found on kgdev once the orphan check stopped "
            "enumerating labels (#298)"
        ),
    ),
)


#: A small graph that satisfies every invariant above. The seeded suite asserts
#: it is clean, so a check that fires on valid data is caught too — an invariant
#: that cannot pass is as useless as one that cannot fail.
CLEAN_GRAPH = """
    CREATE (genus:Taxon {name: 'Faecalibacterium', taxon_id: 'NCBITaxon:216851',
                         rank: 'genus', organization_id: 'test'})
    CREATE (species:Taxon {name: 'Faecalibacterium prausnitzii',
                           taxon_id: 'NCBITaxon:853', rank: 'species',
                           organization_id: 'test'})
    CREATE (species)-[:HAS_PARENT]->(genus)
    CREATE (disease:Disease {name: "Crohn's Disease", disease_id: 'crohn-disease',
                             organization_id: 'test'})
    CREATE (species)-[:ASSOCIATED_WITH_DISEASE {direction: 'down'}]->(disease)
    CREATE (butyrate:Compound {name: 'Butyric acid', hmdb_id: 'HMDB0000039',
                               organization_id: 'test'})
    CREATE (species)-[:PRODUCES]->(butyrate)
"""
