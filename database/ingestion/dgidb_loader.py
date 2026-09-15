"""
DGIdb (Drug-Gene Interaction Database) Loader

Loads curated drug-gene interaction claims from DGIdb v5 into Neo4j.
DGIdb aggregates ~40 upstream sources (ChEMBL, CIViC, PharmGKB's public
claims, TTD, Guide to Pharmacology, ...) into interaction records with a
score, interaction type(s), directionality, supporting publications, and the
citing source(s) for each claim.

License: CC BY 4.0 for DGIdb's own compiled/aggregated API output (this
loader talks to that API, not to any individual upstream source's raw file
directly — see #375's evaluation for why that distinction matters here).

Data source: https://dgidb.org/api/graphql (public GraphQL API, no key
required; verified live 2026-09-10: 27,628 drugs / 11,665 genes, Relay-style
`first`/`after` cursor pagination over the `drugs` connection).
"""

from typing import Optional, Iterator, Dict, Any, List
from .base_loader import APIBasedLoader
import logging

logger = logging.getLogger(__name__)


QUERY = """
query dgidb_drugs($first: Int, $after: String) {
  drugs(first: $first, after: $after) {
    totalCount
    pageInfo {
      hasNextPage
      endCursor
    }
    nodes {
      conceptId
      name
      approved
      interactions {
        gene {
          conceptId
          name
        }
        interactionScore
        interactionTypes {
          type
          directionality
        }
        publications {
          pmid
        }
        sources {
          sourceDbName
          license
        }
      }
    }
  }
}
"""


class DGIdbLoader(APIBasedLoader):
    """
    Load drug-gene interaction claims from the DGIdb v5 GraphQL API.

    Creates:
    - Drug nodes (merged onto the existing ChEMBL-sourced Drug when DGIdb's
      concept ID is a `chembl:<ID>` alias; otherwise a new DGIDB-namespaced
      Drug node — an exact-ID overlap, never a fuzzy name match)
    - Gene nodes (keyed on `name`, an HGNC symbol — matches the `--derive`
      convention so DGIdb enriches the same Gene nodes Reactome derives)
    - (Drug)-[:DGIDB_INTERACTS_WITH]->(Gene) relationships. Deliberately not
      `INTERACTS_WITH` — semmeddb_loader.py already uses that name for
      unrelated mined-literature predicates with a different shape.
    """

    @property
    def source_name(self) -> str:
        return "DGIdb"

    def __init__(
        self,
        driver,
        organization_id: str,
        batch_size: int = 500,
        database: str = "neo4j",
        page_size: int = 500,
        max_drugs: Optional[int] = None,
    ):
        """
        Args:
            driver: Neo4j driver
            organization_id: Organization ID for multi-tenant isolation
            batch_size: Records per batch
            database: Neo4j database name
            page_size: Drugs requested per GraphQL page (API default cap ~1000)
            max_drugs: Stop after scanning this many drugs (None = all ~27.6k)
        """
        super().__init__(
            driver,
            organization_id,
            api_base_url="https://dgidb.org/api/graphql",
            batch_size=batch_size,
            database=database,
            rate_limit_delay=0.2,
        )
        self.page_size = page_size
        self.max_drugs = max_drugs

    # -- extract -----------------------------------------------------------

    def extract(self, **kwargs) -> Iterator[Dict[str, Any]]:
        """Page through DGIdb's `drugs` connection, yielding one record per
        (drug, interaction) pair. Drugs with no interactions are skipped."""
        after = None
        scanned = 0

        while True:
            response = self.fetch_with_retry(
                self.api_base_url,
                method="POST",
                json={"query": QUERY, "variables": {"first": self.page_size, "after": after}},
            )
            drugs = (response.get("data") or {}).get("drugs") or {}
            nodes = drugs.get("nodes") or []

            for drug in nodes:
                scanned += 1
                for interaction in drug.get("interactions") or []:
                    yield {"drug": drug, "interaction": interaction}

                if self.max_drugs and scanned >= self.max_drugs:
                    return

            page_info = drugs.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                return
            after = page_info.get("endCursor")

    # -- transform -----------------------------------------------------------

    def transform(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        drug = record.get("drug") or {}
        interaction = record.get("interaction") or {}
        gene = interaction.get("gene")

        drug_concept_id = drug.get("conceptId")
        if not drug_concept_id or not gene or not gene.get("name"):
            return None

        interaction_types: List[str] = []
        interaction_directions: List[str] = []
        for entry in interaction.get("interactionTypes") or []:
            if entry.get("type"):
                interaction_types.append(entry["type"])
            if entry.get("directionality"):
                interaction_directions.append(entry["directionality"])

        pmids = [p["pmid"] for p in (interaction.get("publications") or []) if p.get("pmid") is not None]

        source_names: List[str] = []
        for source in interaction.get("sources") or []:
            name = source.get("sourceDbName")
            if name and name not in source_names:
                source_names.append(name)

        return {
            "drug_merge_id": self._drug_merge_id(drug_concept_id),
            "drug_name": drug.get("name"),
            "drug_concept_id": drug_concept_id,
            "drug_approved": drug.get("approved"),
            "gene_name": gene["name"],
            "gene_concept_id": gene.get("conceptId"),
            "interaction_score": interaction.get("interactionScore"),
            "interaction_types": interaction_types,
            "interaction_directions": interaction_directions,
            "pmids": pmids,
            "source_names": source_names,
        }

    @staticmethod
    def _drug_merge_id(concept_id: str) -> str:
        """A `chembl:<ID>` concept ID is an exact-ID alias for the existing
        ChEMBL-sourced Drug node (`chembl_loader.py` keys on `CHEMBL:<ID>`),
        so merge onto it. Anything else gets its own DGIDB-namespaced node —
        no fuzzy name matching across drug identity systems."""
        if concept_id.lower().startswith("chembl:"):
            chembl_id = concept_id.split(":", 1)[1]
            return f"CHEMBL:{chembl_id}"
        return f"DGIDB:{concept_id}"

    # -- load ----------------------------------------------------------------

    def load_batch(self, batch: List[Dict[str, Any]]) -> Dict[str, int]:
        if not batch:
            return {"nodes_created": 0, "relationships_created": 0}

        nodes_created = self._load_drugs(batch) + self._load_genes(batch)
        relationships_created = self._load_interactions(batch)

        return {
            "nodes_created": nodes_created,
            "relationships_created": relationships_created,
        }

    def _load_drugs(self, batch: List[Dict[str, Any]]) -> int:
        drugs: Dict[str, Dict[str, Any]] = {}
        for record in batch:
            drug_id = record["drug_merge_id"]
            if drug_id not in drugs:
                drugs[drug_id] = {
                    "drug_id": drug_id,
                    "name": record.get("drug_name"),
                    "dgidb_concept_id": record.get("drug_concept_id"),
                    "approved": record.get("drug_approved"),
                    "organization_id": self.organization_id,
                }

        query = """
            UNWIND $drugs AS d
            MERGE (drug:Drug {drug_id: d.drug_id})
            ON CREATE SET
                drug.name = d.name,
                drug.dgidb_concept_id = d.dgidb_concept_id,
                drug.approved = d.approved,
                drug.organization_id = d.organization_id,
                drug.source = 'dgidb',
                drug.sources = ['dgidb'],
                drug.created_at = datetime()
            ON MATCH SET
                drug.dgidb_concept_id = d.dgidb_concept_id,
                drug.sources = CASE WHEN 'dgidb' IN coalesce(drug.sources, [])
                                     THEN drug.sources
                                     ELSE coalesce(drug.sources, []) + 'dgidb' END,
                drug.updated_at = datetime()
            RETURN count(drug) AS count
        """

        result = self.execute_cypher(query, {"drugs": list(drugs.values())})
        return result[0]["count"] if result else 0

    def _load_genes(self, batch: List[Dict[str, Any]]) -> int:
        genes: Dict[str, Dict[str, Any]] = {}
        for record in batch:
            gene_name = record["gene_name"]
            if gene_name not in genes:
                genes[gene_name] = {
                    "name": gene_name,
                    "organization_id": self.organization_id,
                }

        query = """
            UNWIND $genes AS g
            MERGE (gene:Gene {name: g.name})
            ON CREATE SET
                gene.symbol = g.name,
                gene.organization_id = g.organization_id,
                gene.source = 'dgidb',
                gene.sources = ['dgidb'],
                gene.created_at = datetime()
            ON MATCH SET
                gene.sources = CASE WHEN 'dgidb' IN coalesce(gene.sources, [])
                                     THEN gene.sources
                                     ELSE coalesce(gene.sources, []) + 'dgidb' END,
                gene.updated_at = datetime()
            RETURN count(gene) AS count
        """

        result = self.execute_cypher(query, {"genes": list(genes.values())})
        return result[0]["count"] if result else 0

    def _load_interactions(self, batch: List[Dict[str, Any]]) -> int:
        relationships: Dict[tuple, Dict[str, Any]] = {}
        for record in batch:
            key = (record["drug_merge_id"], record["gene_name"])
            if key not in relationships:
                relationships[key] = {
                    "drug_id": record["drug_merge_id"],
                    "gene_name": record["gene_name"],
                    "interaction_score": record.get("interaction_score"),
                    "interaction_types": record.get("interaction_types", []),
                    "interaction_directions": record.get("interaction_directions", []),
                    "pmids": record.get("pmids", []),
                    "source_names": record.get("source_names", []),
                    "source": "dgidb",
                }

        query = """
            UNWIND $relationships AS r
            MATCH (drug:Drug {drug_id: r.drug_id})
            MATCH (gene:Gene {name: r.gene_name})
            MERGE (drug)-[rel:DGIDB_INTERACTS_WITH]->(gene)
            ON CREATE SET
                rel.interaction_score = r.interaction_score,
                rel.interaction_types = r.interaction_types,
                rel.interaction_directions = r.interaction_directions,
                rel.pmids = r.pmids,
                rel.source_names = r.source_names,
                rel.source = r.source,
                rel.created_at = datetime()
            RETURN count(rel) AS count
        """

        result = self.execute_cypher(query, {"relationships": list(relationships.values())})
        return result[0]["count"] if result else 0


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    from neo4j import GraphDatabase

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
    NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    try:
        loader = DGIdbLoader(driver=driver, organization_id="default", max_drugs=100)
        stats = loader.run()
        print("\nDGIdb Loading Complete!")
        print(f"  Nodes created: {stats.nodes_created}")
        print(f"  Relationships created: {stats.relationships_created}")
        print(f"  Duration: {stats.duration_seconds:.1f} seconds")
    finally:
        driver.close()
