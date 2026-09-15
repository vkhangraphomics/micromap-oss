"""
MicroMap API

Standalone microservice providing access to the microbiome knowledge graph.
"""

import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from api.models import StatisticsResponse, NodeCounts, RelationshipCounts
from api.dependencies import verify_api_key
from api.rate_limit import limiter
from api.scoping import OrgScope, ORG_FILTER, scope_params, resolve_org_scope
from integrations.neo4j_microbiome import MicrobiomeKG

# Configure root logger so app-level logger.info() / logger.warning() /
# logger.exception() calls actually reach stderr (and therefore docker logs).
# Without this, app records hit Python's default `lastResort` handler which
# has a WARNING-level filter and silently drops INFO records — see #176.
# Matches the format already used by database/load_knowledge_graph.py:56.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)

# Initialize KG connection
kg: MicrobiomeKG | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage KG connection lifecycle."""
    global kg

    neo4j_uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user = os.environ.get("NEO4J_USER", "neo4j")
    neo4j_password = os.environ.get("NEO4J_PASSWORD", "password")

    kg = MicrobiomeKG(neo4j_uri, neo4j_user, neo4j_password)
    logger.info("Connected to Neo4j at %s", neo4j_uri)

    # Decision-provenance append-only event log (#190 pillar 2). Idempotent; a
    # no-op when DATABASE_URL is unset (Neo4j-only deploys keep working).
    from api import decision_log
    if decision_log.is_enabled():
        try:
            decision_log.init_schema()
            logger.info("Decision event log schema ready (Postgres)")
        except Exception:
            logger.exception("Failed to initialize decision event log schema")

    # Decision semantic-search vector index (#190 pillar 4). Created to match the
    # configured embedder's dimension; a no-op when embeddings are unconfigured.
    from api import embeddings
    embedder = embeddings.get_embedder()
    if embedder is not None:
        try:
            from api.routes.provenance_decisions import ensure_decision_vector_index
            ensure_decision_vector_index(kg, embedder.dimension)
            logger.info("Decision vector index ready (dim=%d)", embedder.dimension)
        except Exception:
            logger.exception("Failed to create decision vector index")

    yield

    if kg:
        kg.close()
        logger.info("Closed Neo4j connection")


# API docs (/docs, /redoc, /openapi.json) are served in local/dev only. The
# public deployment sets DISABLE_API_DOCS=1 so the interactive docs and the
# OpenAPI schema aren't exposed there — the API reference now lives on the
# Graphomics website. See #288 / docs-consolidation.
_api_docs_enabled = not os.environ.get("DISABLE_API_DOCS")


app = FastAPI(
    title="MicroMap API",
    description="""
## MicroMap - Microbiome Knowledge Graph

MicroMap is Graphomics' microbiome knowledge graph platform, providing comprehensive
access to microbiome-disease associations, taxonomic data, and metabolite relationships.

### Authentication

All `/api/v1/*` endpoints require an API key. Include your key in the `X-API-Key` header:

```bash
curl -H "X-API-Key: your-api-key" http://localhost:8200/api/v1/stats
```

### Rate Limiting

API requests are rate-limited to **100 requests per minute** per API key.

---

### Data Coverage (~1.13M nodes, ~1.42M relationships)

| Entity | Count | Source |
|--------|-------|--------|
| **Taxa** | 1,101,289 | NCBI Taxonomy (Bacteria, Archaea, Fungi, Viruses) |
| **Diseases** | 1,464 | Disbiome, BugSigDB, gutMDisorder, Neurological curation |
| **Metabolites** | 6,534 | HMDB, curated literature |
| **Drugs** | 6,220 | ChEMBL |
| **Pathways** | 1,710 | KEGG, Reactome |
| **Proteins** | 1,659 | ChEMBL |
| **Papers** | 10,000 | PubMed |
| **Studies** | 1,744 | BugSigDB, gutMDisorder |

### Relationships

| Relationship | Count | Description |
|--------------|-------|-------------|
| `HAS_PARENT` | 774,502 | Taxonomic hierarchy |
| `EFFECTIVE_AGAINST` | 276,169 | Antimicrobial resistance |
| `PRODUCES` | 231,556 | Taxon-metabolite production |
| `MENTIONED_IN` | 106,909 | Literature references |
| `ASSOCIATED_WITH_DISEASE` | 11,612 | Taxon-disease links with direction |
| `PARTICIPATES_IN` | 9,443 | Pathway participation |
| `TARGETS` | 4,425 | Drug-protein targets |

### Disease Categories

Gastrointestinal (IBD, Crohn's, IBS), Metabolic (T2D, Obesity), Neurological (Parkinson's,
Alzheimer's, MS, Autism), Cancer (Colorectal), Autoimmune, and others.

---

### Quick Start

**Search for entities:**
```
GET /api/v1/search?q=lactobacillus
```

**Get taxon details:**
```
GET /api/v1/taxa/239935
```

**Get diseases associated with a taxon:**
```
GET /api/v1/taxa/239935/diseases
```

**Get taxa associated with a disease:**
```
GET /api/v1/diseases/parkinson's disease/taxa
```

**Get metabolite producers:**
```
GET /api/v1/metabolites/butyrate/producers
```

**Get full taxonomic lineage:**
```
GET /api/v1/taxa/239935/lineage
```

**Compare taxa metabolite production:**
```
GET /api/v1/taxa/compare?ids=Faecalibacterium,Roseburia,Bifidobacterium
```

**Get metabolites altered in a disease:**
```
GET /api/v1/diseases/parkinson's disease/metabolites
```

**Get pathways a metabolite participates in:**
```
GET /api/v1/metabolites/butyrate/pathways
```

**Search pathways:**
```
GET /api/v1/pathways?search=glycolysis
```

**Compare disease microbiome profiles:**
```
GET /api/v1/diseases/compare?ids=obesity,type 2 diabetes
```

**Search drugs:**
```
GET /api/v1/drugs/search/metformin
```

**Get drug-microbiome disease connections:**
```
GET /api/v1/drugs/CHEMBL1431/diseases
```

**Get papers mentioning a taxon:**
```
GET /api/v1/papers/taxon/239935
```

**Find probiotic candidates for a disease:**
```
GET /api/v1/discovery/probiotics/parkinson's disease
```

**Get cross-feeding network for a taxon:**
```
GET /api/v1/networks/cross-feeding/239935
```

**Find shortest path between entities:**
```
GET /api/v1/graph/path?from=239935&to=butyrate
```

**Get neighborhood of an entity:**
```
GET /api/v1/graph/neighborhood?entity_id=239935&hops=2
```

**Data provenance sources:**
```
GET /api/v1/provenance/sources
```

---

### Documentation
- [Swagger UI](/docs) - Interactive API explorer
- [ReDoc](/redoc) - API reference documentation
""",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if _api_docs_enabled else None,
    redoc_url="/redoc" if _api_docs_enabled else None,
    openapi_url="/openapi.json" if _api_docs_enabled else None,
)

# Add rate limiter to app state and exception handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS configuration — fails closed (#324): unset means no cross-origin
# access, and an explicit "*" is dropped so a stale box .env cannot reopen
# the reflected-origin hole verified live on 2026-08-25.


def parse_cors_origins(value: str | None) -> list[str]:
    """Parse CORS_ORIGINS into an explicit allowlist, refusing wildcards."""
    if not value:
        return []
    origins = [origin.strip() for origin in value.split(",")]
    origins = [origin for origin in origins if origin and origin != "*"]
    if "*" in value:
        logger.warning(
            "CORS_ORIGINS contains '*' — wildcard origins are refused (#324); "
            "set an explicit allowlist"
        )
    return origins


def configure_cors(app: FastAPI, env_value: str | None) -> None:
    """Attach CORS middleware only when an explicit allowlist is configured.

    Credentials stay off: auth is header-based (X-API-Key / Authorization),
    so no browser-ambient credential exists to share — enabling them would
    only arm the serious variant if cookie auth ever appears. Methods and
    headers are pinned to what the API actually serves.
    """
    origins = parse_cors_origins(env_value)
    if not origins:
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "X-API-Key", "Content-Type"],
    )


configure_cors(app, os.environ.get("CORS_ORIGINS"))


@app.get("/health")
async def health_check():
    """
    Health check endpoint (no authentication required).

    Returns the connection status to Neo4j.
    """
    if kg and kg.verify_connection():
        return {"status": "healthy", "neo4j": "connected"}
    return {"status": "unhealthy", "neo4j": "disconnected"}


@app.get(
    "/api/v1/stats",
    response_model=StatisticsResponse,
    dependencies=[Depends(verify_api_key)]
)
@limiter.limit("100/minute")
async def get_kg_stats(request: Request, scope: OrgScope = Depends(resolve_org_scope)):
    """
    Get knowledge graph statistics.

    Returns counts of all nodes and relationships in the graph,
    scoped to the caller's org plus the shared reference data.
    Requires API key authentication.
    """
    if not kg:
        raise HTTPException(status_code=503, detail="KG not available")

    # Get node and relationship counts in a single efficient query.
    # Uses count{} subqueries (Neo4j 5+) with WHERE to scope each count to
    # the caller's org + shared/public orgs (#200 org-read isolation).
    stats_query = f"""
    RETURN
        count {{ (t:Taxon) WHERE {ORG_FILTER("t")} }} AS taxa,
        count {{ (d:Disease) WHERE {ORG_FILTER("d")} }} AS diseases,
        count {{ (m:Compound) WHERE {ORG_FILTER("m")} }} AS metabolites,
        count {{ (p:Pathway) WHERE {ORG_FILTER("p")} }} AS pathways,
        count {{ (dr:Drug) WHERE {ORG_FILTER("dr")} }} AS drugs,
        count {{ (g:Gene) WHERE {ORG_FILTER("g")} }} AS genes,
        count {{ (pr:Protein) WHERE {ORG_FILTER("pr")} }} AS proteins,
        count {{ (pa:Paper) WHERE {ORG_FILTER("pa")} }} AS papers,
        count {{ (s0)-[:ASSOCIATED_WITH_DISEASE]->() WHERE {ORG_FILTER("s0")} }} AS disease_associations,
        count {{ (s1)-[:PRODUCES]->() WHERE {ORG_FILTER("s1")} }} AS produces,
        count {{ (s2)-[:HAS_PARENT]->() WHERE {ORG_FILTER("s2")} }} AS taxonomy_hierarchy,
        count {{ (s3)-[:PARTICIPATES_IN]->() WHERE {ORG_FILTER("s3")} }} AS participates_in,
        count {{ (s4)-[:TARGETS]->() WHERE {ORG_FILTER("s4")} }} AS targets,
        count {{ (s5)-[:MENTIONED_IN]->() WHERE {ORG_FILTER("s5")} }} AS mentioned_in,
        count {{ (s6)-[:LINKED_TO_DISEASE]->() WHERE {ORG_FILTER("s6")} }} AS linked_to_disease,
        count {{ (s7)-[:EFFECTIVE_AGAINST]->() WHERE {ORG_FILTER("s7")} }} AS effective_against,
        count {{ (s8)-[:BELONGS_TO_CLASS]->() WHERE {ORG_FILTER("s8")} }} AS belongs_to_class,
        count {{ (s9)-[:PROCESSES]->() WHERE {ORG_FILTER("s9")} }} AS processes,
        count {{ (s10)-[:SAME_AS]->() WHERE {ORG_FILTER("s10")} }} AS same_as
    """

    stats_result = kg.execute_cypher(stats_query, scope_params(scope))
    if not stats_result:
        raise HTTPException(status_code=500, detail="Failed to get statistics")

    row = stats_result[0]
    nodes = row
    rels = row

    total_nodes = sum(nodes[k] for k in ["taxa", "diseases", "metabolites", "pathways", "drugs", "genes", "proteins", "papers"])
    total_rels = sum(rels[k] for k in ["disease_associations", "produces", "taxonomy_hierarchy", "participates_in", "targets", "mentioned_in", "linked_to_disease", "effective_against", "belongs_to_class", "processes", "same_as"])

    return StatisticsResponse(
        nodes=NodeCounts(
            taxa=nodes["taxa"],
            diseases=nodes["diseases"],
            metabolites=nodes["metabolites"],
            pathways=nodes["pathways"],
            drugs=nodes["drugs"],
            genes=nodes["genes"],
            proteins=nodes["proteins"],
            papers=nodes["papers"],
        ),
        relationships=RelationshipCounts(
            disease_associations=rels["disease_associations"],
            produces=rels["produces"],
            taxonomy_hierarchy=rels["taxonomy_hierarchy"],
            participates_in=rels["participates_in"],
            targets=rels["targets"],
            mentioned_in=rels["mentioned_in"],
            linked_to_disease=rels["linked_to_disease"],
            effective_against=rels["effective_against"],
            belongs_to_class=rels["belongs_to_class"],
            processes=rels["processes"],
            same_as=rels["same_as"],
        ),
        total_nodes=total_nodes,
        total_relationships=total_rels,
    )


# Import additional routes
from api.routes import taxa, diseases, search, metabolites
from api.routes import drugs, genes, proteins, pathways, biomarkers, papers, networks, discovery, provenance, graph
from api.routes import provenance_decisions, federation, provenance_lineage, provenance_artifacts, provenance_chain

# Include routers with API key authentication
app.include_router(
    taxa.router,
    prefix="/api/v1",
    tags=["Taxa"],
    dependencies=[Depends(verify_api_key)]
)
app.include_router(
    diseases.router,
    prefix="/api/v1",
    tags=["Diseases"],
    dependencies=[Depends(verify_api_key)]
)
app.include_router(
    metabolites.router,
    prefix="/api/v1",
    tags=["Metabolites"],
    dependencies=[Depends(verify_api_key)]
)
app.include_router(
    search.router,
    prefix="/api/v1",
    tags=["Search"],
    dependencies=[Depends(verify_api_key)]
)
app.include_router(
    drugs.router,
    prefix="/api/v1",
    tags=["Drugs"],
    dependencies=[Depends(verify_api_key)]
)
app.include_router(
    genes.router,
    prefix="/api/v1",
    tags=["Genes"],
    dependencies=[Depends(verify_api_key)]
)
app.include_router(
    proteins.router,
    prefix="/api/v1",
    tags=["Proteins"],
    dependencies=[Depends(verify_api_key)]
)
app.include_router(
    pathways.router,
    prefix="/api/v1",
    tags=["Pathways"],
    dependencies=[Depends(verify_api_key)]
)
app.include_router(
    biomarkers.router,
    prefix="/api/v1",
    tags=["Biomarkers"],
    dependencies=[Depends(verify_api_key)]
)
app.include_router(
    papers.router,
    prefix="/api/v1",
    tags=["Papers"],
    dependencies=[Depends(verify_api_key)]
)
app.include_router(
    networks.router,
    prefix="/api/v1",
    tags=["Networks"],
    dependencies=[Depends(verify_api_key)]
)
app.include_router(
    discovery.router,
    prefix="/api/v1",
    tags=["Discovery"],
    dependencies=[Depends(verify_api_key)]
)
app.include_router(
    provenance.router,
    prefix="/api/v1",
    tags=["Provenance"],
    dependencies=[Depends(verify_api_key)]
)
app.include_router(
    provenance_decisions.router,
    prefix="/api/v1",
    tags=["Decision Provenance"],
    dependencies=[Depends(verify_api_key)]
)
app.include_router(
    graph.router,
    prefix="/api/v1",
    tags=["Graph Traversal"],
    dependencies=[Depends(verify_api_key)]
)
app.include_router(
    federation.router,
    prefix="/api/v1",
    tags=["Federation"],
)
app.include_router(
    provenance_lineage.router,
    tags=["Provenance Lineage"],
    dependencies=[Depends(verify_api_key)],
)
app.include_router(
    provenance_artifacts.router,
    tags=["Provenance Lineage"],
    dependencies=[Depends(verify_api_key)],
)
app.include_router(
    provenance_chain.router,
    prefix="/api/v1",
    tags=["Decision Provenance"],
    dependencies=[Depends(verify_api_key)],
)
