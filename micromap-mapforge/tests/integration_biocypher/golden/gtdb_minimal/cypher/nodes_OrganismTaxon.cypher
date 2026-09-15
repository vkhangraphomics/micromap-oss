// Generated Cypher — OrganismTaxon nodes (parameterized; see nodes_OrganismTaxon.params.json)

UNWIND $batch_id AS row
MERGE (n:OrganismTaxon {id: row.merge_value, organization_id: $organization_id})
SET n += row.props;
