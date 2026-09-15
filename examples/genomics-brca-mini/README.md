# genomics-brca-mini

MapForge example bundle: BRCA1/BRCA2 pathogenic ClinVar variants.

**Source:** NCBI ClinVar
**Filter:** BRCA1 + BRCA2 genes, Pathogenic clinical significance
**Template:** `genomics`
**Rows:** ~30

## Pipeline Walkthrough

```bash
cd examples/genomics-brca-mini

mapforge resolve --bundle . --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j --neo4j-password your-password

mapforge plan --bundle . --organization-id demo \
  --policy routing-policy.yaml --contributor contributor.yaml

mapforge emit --bundle .
mapforge approve --bundle . --reviewer your-name

mapforge submit --bundle . --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j --neo4j-password your-password
```

After submit:

```cypher
MATCH (v:Variant)-[:LOCATED_IN_GENE]->(g:Gene)
WHERE g.symbol IN ['BRCA1', 'BRCA2']
RETURN g.symbol, count(v) AS variant_count
ORDER BY variant_count DESC
```
