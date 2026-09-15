// Generated Cypher — Taxon nodes (parameterized; see nodes_Taxon.params.json)

UNWIND $batch_ncbi_tax_id AS row
MATCH (n:Taxon {ncbi_tax_id: row.merge_value})
SET n += row.props;
