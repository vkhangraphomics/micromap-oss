# MicroMap

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.22800749.svg)](https://doi.org/10.5281/zenodo.22800749)
[![KG-Registry](https://img.shields.io/badge/KG--Registry-listed-blue)](https://kghub.org/kg-registry/resource/micromap/micromap.html)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Graphomics' microbiome knowledge graph platform, providing comprehensive access to microbiome-disease associations, taxonomic data, and metabolite relationships.

> **Not to be confused with** the "MicroMap" microbiome-metabolism network
> visualization resource from the Thiele lab (University of Galway), published
> in [*npj Biofilms and Microbiomes*](https://www.nature.com/articles/s41522-025-00853-0)
> (2025) and hosted on [Harvard Dataverse](https://dataverse.harvard.edu/dataverse/micromap).
> That project visualizes genome-scale metabolic reconstructions; this one is
> a Neo4j-backed knowledge graph + ingestion platform. Unrelated projects,
> same name, same field — no affiliation between them.

## Quick Start

1. **Clone and configure:**
   ```bash
   cp .env.example .env
   # Edit .env with your settings
   ```

2. **Start the services:**
   ```bash
   docker compose up -d
   ```

3. **Load data:**
   ```bash
   # Load all data sources
   docker compose exec micromap-api python -m database.load_knowledge_graph --all

   # Or load specific sources
   docker compose exec micromap-api python -m database.load_knowledge_graph --taxonomy
   docker compose exec micromap-api python -m database.load_knowledge_graph --disbiome
   ```

4. **Access the API:**
   - API: http://localhost:8200
   - API Docs: http://localhost:8200/docs
   - Neo4j Browser: http://localhost:7474

## Authentication

All `/api/v1/*` endpoints require an API key. Include your key in the `X-API-Key` header:

```bash
curl -H "X-API-Key: your-api-key" http://localhost:8200/api/v1/stats
```

Rate limit: **100 requests per minute** per API key.

## API Endpoints

For full API documentation, see the [Graphomics docs](https://graphomics.com/docs).

### Core

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check (no auth required) |
| `GET /api/v1/stats` | Knowledge graph statistics |

### Search

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/search?q=query` | Full-text search across all entity types |
| `GET /api/v1/search/suggest?q=query` | Autocomplete suggestions |
| `GET /api/v1/search/counts?q=query` | Match counts by entity type |

### Taxa

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/taxa` | List taxa (filter by rank, kingdom, name) |
| `GET /api/v1/taxa/{id}` | Get taxon details |
| `GET /api/v1/taxa/{id}/children` | Child taxa in hierarchy |
| `GET /api/v1/taxa/{id}/diseases` | Associated diseases |
| `GET /api/v1/taxa/{id}/metabolites` | Produced metabolites |
| `GET /api/v1/taxa/{id}/lineage` | Full taxonomic lineage |
| `GET /api/v1/taxa/search/{query}` | Search taxa by name |
| `GET /api/v1/taxa/compare?ids=...` | Compare metabolite production across taxa |

### Diseases

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/diseases` | List all diseases with taxa counts |
| `GET /api/v1/diseases/{id}` | Disease details with top associated taxa |
| `GET /api/v1/diseases/{id}/taxa` | All taxa associated with a disease |
| `GET /api/v1/diseases/{id}/related` | Diseases with similar microbiome profiles |
| `GET /api/v1/diseases/{id}/metabolites` | Metabolites linked via producing taxa |
| `GET /api/v1/diseases/compare?ids=...` | Compare microbiome profiles between diseases |

### Metabolites

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/metabolites` | List metabolites (filter by name, category) |
| `GET /api/v1/metabolites/{id}` | Metabolite details |
| `GET /api/v1/metabolites/{id}/producers` | Taxa that produce this metabolite |
| `GET /api/v1/metabolites/{id}/diseases` | Diseases linked via producing taxa |
| `GET /api/v1/metabolites/{id}/pathways` | Pathways this metabolite participates in |
| `GET /api/v1/metabolites/categories/list` | Metabolite categories with counts |

### Drugs

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/drugs` | List drugs |
| `GET /api/v1/drugs/{id}` | Drug details |
| `GET /api/v1/drugs/{id}/taxa` | Taxa that metabolize/interact with a drug |
| `GET /api/v1/drugs/{id}/diseases` | Disease connections via microbiome |
| `GET /api/v1/drugs/search/{query}` | Search drugs by name |

### Genes

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/genes` | List genes |
| `GET /api/v1/genes/{id}` | Gene details |
| `GET /api/v1/genes/{id}/taxa` | Taxa with this gene |
| `GET /api/v1/genes/{id}/pathways` | Pathways this gene participates in |
| `GET /api/v1/genes/search/{query}` | Search genes by name |

### Proteins

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/proteins` | List proteins |
| `GET /api/v1/proteins/{id}` | Protein details |
| `GET /api/v1/proteins/{id}/drugs` | Drugs targeting this protein |

### Pathways

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/pathways` | List pathways with metabolite counts |
| `GET /api/v1/pathways/{id}` | Pathway details |
| `GET /api/v1/pathways/{id}/metabolites` | Metabolites in this pathway |
| `GET /api/v1/pathways/{id}/genes` | Genes in this pathway |
| `GET /api/v1/pathways/{id}/taxa` | Taxa linked via metabolites or genes |
| `GET /api/v1/pathways/search/{query}` | Search pathways by name |

### Biomarkers

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/biomarkers` | List biomarker signatures |
| `GET /api/v1/biomarkers/{id}` | Signature details with features and metrics |
| `GET /api/v1/biomarkers/disease/{id}` | Best signatures for a disease |

### Papers

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/papers` | List papers (filter by year, title) |
| `GET /api/v1/papers/{pmid}` | Paper details with mentioned entities |
| `GET /api/v1/papers/taxon/{id}` | Papers mentioning a taxon |
| `GET /api/v1/papers/disease/{id}` | Papers related to a disease |
| `GET /api/v1/papers/search/{query}` | Search papers by title/abstract |

### Networks

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/networks/cross-feeding` | Cross-feeding network between taxa |
| `GET /api/v1/networks/cross-feeding/{id}` | Cross-feeding partners for a taxon |

### Discovery

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/discovery/probiotics/{disease_id}` | Probiotic candidates for a disease |
| `GET /api/v1/discovery/scfa-producers` | SCFA-producing taxa |

### Provenance

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/provenance/sources` | Data sources with entity counts |
| `GET /api/v1/provenance/entity/{id}` | Provenance for a specific entity |
| `GET /api/v1/provenance/stats` | Coverage metrics by label |

### Graph Traversal

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/graph/path` | Shortest path between two entities |
| `GET /api/v1/graph/connections` | All shortest paths between two entities |
| `GET /api/v1/graph/neighborhood` | Entities within N hops of a given entity |

## Connecting from Other Applications

Set these environment variables in your application:

```bash
NEO4J_URI=bolt://your-micromap-server:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-password
```

Or use the REST API:

```python
import requests

response = requests.get("http://your-micromap-server:8200/api/v1/diseases/diabetes/taxa")
taxa = response.json()["taxa"]
```

## Data Sources

| Source | Description | Key Stats |
|--------|-------------|-----------|
| NCBI Taxonomy | Microbial taxonomy hierarchy | 1,101,289 taxa |
| Disbiome | Disease-microbe associations | Disease-microbe links |
| BugSigDB | Microbial differential abundance signatures | Study-based signatures |
| gutMDisorder | Gut microbiota-disorder associations | Gut-disorder links |
| HMDB | Human metabolites | 6,534 metabolites |
| KEGG | Metabolic pathways | 1,710 pathways |
| ChEMBL | Drugs and protein targets | 6,220 drugs, 1,659 proteins |
| Reactome | Pathway and protein data | Pathway integration |
| PubMed | Scientific literature | 10,000 papers |
| PubChem | Chemical compounds | 32 compounds |
| Curated PRODUCES | Taxon-metabolite production | 231,556 relationships |
| Neurological diseases | Gut-brain axis associations | Curated associations |

> Counts above are point-in-time snapshots and drift as new data loads — check
> `GET /api/v1/stats` for current live counts rather than treating this table
> as authoritative.

## Schema

### Node Types
- `Taxon` - Microbial organisms
- `Disease` - Human diseases
- `Compound` - Chemical compounds and metabolites, from HMDB/PubChem/KEGG/etc.
  (the canonical label — there is no separate `Metabolite` label)
- `Pathway` - Metabolic pathways
- `Gene` - Genetic information (derived from `Protein.gene_name`, see below)
- `Drug` - Pharmaceutical compounds
- `Protein` - Protein targets
- `Paper` - Scientific literature
- `Study` - Research studies
- `BodySite` - Body site/habitat locations
- `DrugClass` - Drug classifications

### Relationships
- `HAS_PARENT` - Taxonomic hierarchy
- `EFFECTIVE_AGAINST` - Antimicrobial resistance
- `PRODUCES` - Taxon → Compound
- `MENTIONED_IN` - Entity → Paper
- `ASSOCIATED_WITH_DISEASE` - Taxon → Disease
- `PARTICIPATES_IN` - Compound/Gene → Pathway
- `TARGETS` - Drug → Protein
- `DGIDB_INTERACTS_WITH` - Drug ↔ Gene interaction (kept distinct from `INTERACTS_WITH`, which is used for unrelated mined-literature predicates)
- `ENCODED_BY` - Protein → Gene (derived via the `--derive` maintenance flag)
- `BELONGS_TO_CLASS` - Drug → DrugClass
- `PROCESSES` - Metabolic processing
- `LINKED_TO_DISEASE` - Compound → Disease
- `FOUND_IN` - Taxon → BodySite
- `SAME_AS` - Entity equivalence
- `IMPLICATED_IN` - Pathway → Disease (derived mechanistic spine, via `--derive`)

## Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally
uvicorn api.main:app --reload --port 8200

# Run tests
pytest tests/
```

## Citing MicroMap

If you use MicroMap in your research, please cite it — see
[`CITATION.cff`](CITATION.cff), or use the DOI directly:
[10.5281/zenodo.22800749](https://doi.org/10.5281/zenodo.22800749).

MicroMap is also listed in the [KG-Registry](https://kghub.org/kg-registry/resource/micromap/micromap.html),
a community registry of knowledge graphs.

## License

MIT — see [`LICENSE`](LICENSE).
