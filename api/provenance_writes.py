"""#310: Cypher for the :Artifact cross-run lineage writes.

Kept out of the route module on purpose. The phantom-relationship guard
(tests/api/test_phantom_relationship_types.py) treats routes as readers and
scans only non-route modules for writers, so a relationship type written solely
inside a route reads as a phantom. These MERGE literals are genuine writers of
PRODUCED / USED / DERIVED_FROM — home them here (a non-route module) so the
guard sees them, exactly as the MapForge provenance package holds the ABOUT /
RECORDED / SUPERSEDED_BY writers. provenance_decisions.py imports and executes
them.
"""

# MERGE the :Artifact (keyed on the caller's org + content hash) and link the
# decision that produced/used it. INPUT -> USED, OUTPUT -> PRODUCED; a
# checksummed entry with no/other role still records the artifact identity but
# no directional edge (we do not guess a direction).
ARTIFACT_MERGE_CYPHER = """
MATCH (d:Decision {id: $id, organization_id: $org})
UNWIND $artifacts AS art
MERGE (a:Artifact {organization_id: $org, sha256: art.checksum})
  ON CREATE SET a.uri = art.uri, a.first_seen = datetime()
  ON MATCH SET a.uri = coalesce(a.uri, art.uri)
FOREACH (_ IN CASE WHEN art.role = 'INPUT' THEN [1] ELSE [] END |
         MERGE (d)-[:USED]->(a))
FOREACH (_ IN CASE WHEN art.role = 'OUTPUT' THEN [1] ELSE [] END |
         MERGE (d)-[:PRODUCED]->(a))
RETURN count(DISTINCT a) AS artifacts
"""

# The output artifacts of THIS decision are DERIVED_FROM its input artifacts
# (PROV: an output was derived from the activity's inputs). A later run that
# USES one of these outputs then traces back through it — cross-run lineage.
DERIVED_FROM_CYPHER = """
MATCH (d:Decision {id: $id})-[:PRODUCED]->(out:Artifact),
      (d)-[:USED]->(inp:Artifact)
WHERE out <> inp
MERGE (out)-[r:DERIVED_FROM]->(inp)
RETURN count(r) AS derived
"""
