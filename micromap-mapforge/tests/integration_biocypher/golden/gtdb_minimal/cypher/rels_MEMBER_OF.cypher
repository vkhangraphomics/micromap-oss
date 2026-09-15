// Generated Cypher — MEMBER_OF relationships (parameterized; see rels_MEMBER_OF.params.json)

UNWIND $batch_id__id AS row
MATCH (a:OrganismTaxon {id: row.from}),
      (b:OrganismTaxon {id: row.to})
MERGE (a)-[r:MEMBER_OF {organization_id: $organization_id}]->(b)
SET r += row.props;
