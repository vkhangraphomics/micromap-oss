# proteomics-plasma-mini

MapForge example bundle: UniProt Swiss-Prot human plasma proteins.

**Source:** UniProt Swiss-Prot (reviewed human proteome)  
**Filter:** Plasma/secreted proteins with disease annotation + KEGG pathway  
**Template:** `proteomics`  
**Rows:** 30

## Pipeline Walkthrough

```bash
cd examples/proteomics-plasma-mini

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
MATCH (p:Protein)-[:ASSOCIATED_WITH_DISEASE]->(d:Disease),
      (g:Gene)-[:ENCODES]->(p)
RETURN g.symbol, p.name, d.name
ORDER BY g.symbol
LIMIT 20
```
