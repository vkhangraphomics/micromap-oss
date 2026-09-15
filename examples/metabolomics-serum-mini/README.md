# metabolomics-serum-mini

MapForge example bundle: HMDB serum metabolites with disease and pathway annotations.

**Source:** Human Metabolome Database (HMDB)  
**Filter:** Serum biofluid (UBERON:0001977), disease-associated metabolites with KEGG pathway  
**Template:** `metabolomics`  
**Rows:** 30

## Pipeline Walkthrough

```bash
cd examples/metabolomics-serum-mini

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
MATCH (c:Compound)-[:BIOMARKER_FOR]->(d:Disease)
WHERE d.name_normalized CONTAINS 'alzheimer'
RETURN c.name, d.name
```
