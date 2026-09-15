# transcriptomics-alzheimer-mini

MapForge example bundle: GEO GSE5281 Alzheimer's disease DEGs.

**Source:** NCBI GEO, accession GSE5281 (Liang et al. 2008, PMID 18632887)  
**Filter:** Top DEGs (adj_p < 0.05) across 6 brain regions  
**Template:** `transcriptomics`  
**Rows:** 30 (gene x brain-region pairs)

## Pipeline Walkthrough

```bash
cd examples/transcriptomics-alzheimer-mini

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
MATCH (g:Gene)-[r:DIFFERENTIALLY_EXPRESSED_IN]->(c:Condition {efo_id: 'EFO:0000249'})
RETURN g.symbol, count(r) AS region_count, avg(r.log2_fc) AS avg_l2fc
ORDER BY avg_l2fc DESC LIMIT 10
```
