// Generated Cypher — Disease nodes (parameterized; see nodes_Disease.params.json)

UNWIND $batch_doid AS row
MATCH (n:Disease {doid: row.merge_value})
SET n += row.props;
UNWIND $batch_name__contributor AS row
MERGE (n:Disease {name: row.merge_value, organization_id: $organization_id})
SET n += row.props;
