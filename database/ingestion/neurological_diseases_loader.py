"""
Neurological/Psychiatric Diseases - Curated Microbiome Associations Loader

Adds well-established microbiome associations for neurological and psychiatric
diseases, focusing on gut-brain axis research. Data is curated from meta-analyses
and systematic reviews.

Key sources:
- Systematic reviews on gut-brain axis
- Meta-analyses of microbiome studies in neurological diseases
- PubMed search validated findings

Note: These associations supplement existing Disbiome data with additional
neurological/psychiatric conditions relevant for research.
"""

import os
import logging
from typing import Dict, List, Any, Iterator, Optional
from neo4j import Driver

from .base_loader import BaseLoader, normalize_disease_name

logger = logging.getLogger(__name__)


# =============================================================================
# CURATED NEUROLOGICAL DISEASE-MICROBIOME ASSOCIATIONS
# =============================================================================
# Format: {disease_name: [(taxon_name, direction, evidence_level, pmids, notes)]}
# direction: "enriched", "depleted", "altered"
# evidence_level: "high" (multiple studies/meta-analysis), "medium" (few studies), "low" (single study)

NEUROLOGICAL_DISEASE_ASSOCIATIONS = {
    # =========================================================================
    # Major Depressive Disorder (MDD)
    # =========================================================================
    "Major Depressive Disorder": [
        ("Bacteroides", "enriched", "high", ["32943178", "32190896"], "Consistently increased in MDD across studies"),
        ("Alistipes", "enriched", "high", ["32943178", "31563401"], "Pro-inflammatory; associated with depression severity"),
        ("Parabacteroides", "enriched", "medium", ["31563401"], "Elevated in treatment-resistant depression"),
        ("Faecalibacterium", "depleted", "high", ["32943178", "32190896"], "Anti-inflammatory butyrate producer; reduced in MDD"),
        ("Lactobacillus", "depleted", "medium", ["31563401"], "Probiotic bacteria; lower in depression"),
        ("Bifidobacterium", "depleted", "high", ["32943178"], "Psychobiotic; reduced in MDD patients"),
        ("Coprococcus", "depleted", "high", ["30718848"], "Quality of life predictor; depleted in depression"),
        ("Dialister", "depleted", "high", ["30718848"], "Associated with mental quality of life"),
        ("Prevotella", "depleted", "medium", ["32190896"], "Fiber-degrading bacteria; lower in MDD"),
        ("Ruminococcus", "altered", "medium", ["32943178"], "Variable findings across studies"),
    ],

    # =========================================================================
    # ADHD (Attention Deficit Hyperactivity Disorder)
    # =========================================================================
    "Attention Deficit Hyperactivity Disorder": [
        ("Faecalibacterium prausnitzii", "depleted", "medium", ["28253233", "30282964"], "Butyrate producer; lower in ADHD"),
        ("Bifidobacterium", "depleted", "medium", ["28253233"], "Reduced in ADHD children"),
        ("Bacteroides", "enriched", "medium", ["30282964"], "Associated with ADHD symptoms"),
        ("Neisseria", "enriched", "low", ["28253233"], "Elevated in some ADHD cohorts"),
        ("Ruminococcus", "altered", "low", ["30282964"], "Variable across studies"),
    ],

    # =========================================================================
    # ALS (Amyotrophic Lateral Sclerosis)
    # =========================================================================
    "Amyotrophic Lateral Sclerosis": [
        ("Akkermansia muciniphila", "depleted", "medium", ["31330533", "32816257"], "Protective mucin-degrader; lower in ALS"),
        ("Escherichia", "enriched", "medium", ["31330533"], "Pro-inflammatory; elevated in ALS"),
        ("Enterobacteriaceae", "enriched", "medium", ["32816257"], "Increased Gram-negative bacteria"),
        ("Ruminococcus", "depleted", "medium", ["31330533"], "Fiber-degrading; reduced in ALS"),
        ("Faecalibacterium", "depleted", "low", ["32816257"], "Anti-inflammatory bacteria lower"),
    ],

    # =========================================================================
    # Huntington's Disease
    # =========================================================================
    "Huntington's Disease": [
        ("Bacteroides", "enriched", "medium", ["34421089"], "Elevated in HD patients"),
        ("Lactobacillus", "depleted", "low", ["34421089"], "Probiotic bacteria reduced"),
        ("Akkermansia", "depleted", "low", ["34421089"], "Gut barrier protective species lower"),
    ],

    # =========================================================================
    # Post-Traumatic Stress Disorder (PTSD)
    # =========================================================================
    "Post-Traumatic Stress Disorder": [
        ("Actinobacteria", "depleted", "medium", ["29260219"], "Lower microbial diversity"),
        ("Verrucomicrobia", "depleted", "low", ["29260219"], "Reduced gut barrier bacteria"),
        ("Bacteroides", "enriched", "medium", ["29260219"], "Pro-inflammatory shift"),
    ],

    # =========================================================================
    # Chronic Fatigue Syndrome / ME
    # =========================================================================
    "Chronic Fatigue Syndrome": [
        ("Faecalibacterium prausnitzii", "depleted", "high", ["31324981", "27669521"], "Consistent reduction; anti-inflammatory"),
        ("Bifidobacterium", "depleted", "medium", ["31324981"], "Lower in CFS patients"),
        ("Enterococcus", "enriched", "medium", ["27669521"], "Elevated in CFS"),
        ("Streptococcus", "enriched", "medium", ["27669521"], "Increased in CFS patients"),
    ],

    # =========================================================================
    # Migraine
    # =========================================================================
    "Migraine": [
        ("Clostridium", "enriched", "medium", ["32973594"], "Nitrate-reducing bacteria associated with migraines"),
        ("Haemophilus", "enriched", "low", ["32973594"], "Elevated in migraine patients"),
        ("Faecalibacterium", "depleted", "medium", ["32973594"], "Anti-inflammatory; lower in migraineurs"),
    ],

    # =========================================================================
    # Obsessive-Compulsive Disorder (OCD)
    # =========================================================================
    "Obsessive-Compulsive Disorder": [
        ("Lactobacillus", "depleted", "medium", ["31836716"], "Probiotic bacteria reduced in OCD"),
        ("Oscillospira", "enriched", "low", ["31836716"], "Elevated in OCD patients"),
        ("Anaerostipes", "depleted", "low", ["31836716"], "Butyrate producer; reduced"),
    ],

    # =========================================================================
    # Eating Disorders (Anorexia Nervosa)
    # =========================================================================
    "Anorexia Nervosa": [
        ("Methanobrevibacter smithii", "enriched", "high", ["26546837", "28180096"], "Methane producer; consistently elevated"),
        ("Roseburia", "depleted", "medium", ["28180096"], "Butyrate producer; lower in AN"),
        ("Bacteroides", "depleted", "medium", ["26546837"], "Reduced in underweight patients"),
    ],

    # =========================================================================
    # Stroke
    # =========================================================================
    "Stroke": [
        ("Enterobacteriaceae", "enriched", "high", ["31257137", "32179758"], "Post-stroke gut dysbiosis"),
        ("Akkermansia", "depleted", "medium", ["31257137"], "Gut barrier disruption after stroke"),
        ("Lactobacillus", "depleted", "medium", ["32179758"], "Reduced beneficial bacteria"),
        ("Bacteroides", "enriched", "medium", ["31257137"], "Pro-inflammatory shift"),
    ],

    # =========================================================================
    # Traumatic Brain Injury
    # =========================================================================
    "Traumatic Brain Injury": [
        ("Proteobacteria", "enriched", "medium", ["31649251"], "Post-TBI gut dysbiosis"),
        ("Akkermansia", "depleted", "low", ["31649251"], "Gut barrier dysfunction"),
        ("Lactobacillus", "depleted", "medium", ["31649251"], "Probiotic depletion post-injury"),
    ],
}


class NeurologicalDiseasesLoader(BaseLoader):
    """
    Loader for curated neurological disease-microbiome associations.

    Creates Disease and Taxon nodes (if not existing) and
    (Taxon)-[:ASSOCIATED_WITH_DISEASE]->(Disease) relationships.
    """

    def __init__(
        self,
        driver: Driver,
        organization_id: str,
        batch_size: int = 100,
        database: str = "neo4j"
    ):
        super().__init__(driver, organization_id, batch_size, database)

    @property
    def source_name(self) -> str:
        return "curated_neurological"

    def extract(self, **kwargs) -> Iterator[Dict[str, Any]]:
        """Extract associations from curated data."""
        for disease, associations in NEUROLOGICAL_DISEASE_ASSOCIATIONS.items():
            for taxon, direction, evidence, pmids, notes in associations:
                yield {
                    'disease': disease,
                    'taxon': taxon,
                    'direction': direction,
                    'evidence_level': evidence,
                    'pmids': pmids,
                    'notes': notes
                }

    def transform(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Transform record for loading."""
        return {
            'disease_name': record['disease'],
            'disease_normalized': normalize_disease_name(record['disease']),
            'taxon_name': record['taxon'],
            'direction': record['direction'],
            'evidence_level': record['evidence_level'],
            'pmids': record['pmids'],
            'notes': record['notes']
        }

    def load_batch(self, batch: List[Dict[str, Any]]) -> Dict[str, int]:
        """Load batch of associations."""
        nodes_created = 0
        rels_created = 0

        for record in batch:
            # Ensure Disease node exists
            disease_query = """
                MERGE (d:Disease {name_normalized: $disease_normalized})
                ON CREATE SET
                    d.name = $disease_name,
                    d.source = 'curated_neurological',
                    d.sources = ['curated_neurological'],
                    d.organization_id = $org_id,
                    d.category = 'neurological',
                    d.created_at = datetime()
                ON MATCH SET
                    d.updated_at = datetime()
                RETURN d
            """
            self.execute_cypher(disease_query, {
                'disease_normalized': record['disease_normalized'],
                'disease_name': record['disease_name'],
                'org_id': self.organization_id
            })
            nodes_created += 1

            # Find or create Taxon node and create association.
            # (An earlier APOC-based variant was kept here as `assoc_query`
            # but never executed; removed in the post-#162 ruff sweep —
            # `simple_assoc_query` below is the live path.)
            simple_assoc_query = """
                // Ensure disease exists
                MERGE (d:Disease {name_normalized: $disease_normalized})
                ON CREATE SET
                    d.name = $disease_name,
                    d.source = 'curated_neurological',
                    d.sources = ['curated_neurological'],
                    d.organization_id = $org_id,
                    d.category = 'neurological',
                    d.created_at = datetime()

                // Find existing taxon or create new one
                WITH d
                OPTIONAL MATCH (existing:Taxon)
                WHERE existing.name = $taxon_name
                   OR toLower(existing.name) = toLower($taxon_name)
                   OR existing.name STARTS WITH $taxon_name
                   OR existing.genus = $taxon_name

                WITH d, collect(existing)[0] as t

                // Create association
                FOREACH (ignore IN CASE WHEN t IS NOT NULL THEN [1] ELSE [] END |
                    MERGE (t)-[r:ASSOCIATED_WITH_DISEASE]->(d)
                    ON CREATE SET
                        r.direction = $direction,
                        r.evidence_level = $evidence_level,
                        r.pmids = $pmids,
                        r.notes = $notes,
                        r.source = 'curated_neurological',
                        r.created_at = datetime()
                    ON MATCH SET
                        r.updated_at = datetime()
                )

                RETURN CASE WHEN t IS NOT NULL THEN 1 ELSE 0 END as created
            """

            try:
                result = self.execute_cypher(simple_assoc_query, {
                    'disease_normalized': record['disease_normalized'],
                    'disease_name': record['disease_name'],
                    'taxon_name': record['taxon_name'],
                    'direction': record['direction'],
                    'evidence_level': record['evidence_level'],
                    'pmids': record['pmids'],
                    'notes': record['notes'],
                    'org_id': self.organization_id
                })
                if result and result[0].get('created', 0) > 0:
                    rels_created += 1
            except Exception as e:
                logger.warning(f"Failed to create association for {record['taxon_name']} -> {record['disease_name']}: {e}")

        return {
            'nodes_created': nodes_created,
            'relationships_created': rels_created
        }


def load_neurological_diseases(
    driver: Driver,
    organization_id: str = "default"
) -> Dict[str, Any]:
    """
    Convenience function to load curated neurological disease associations.

    Args:
        driver: Neo4j driver instance
        organization_id: Organization ID

    Returns:
        Loading statistics
    """
    loader = NeurologicalDiseasesLoader(
        driver=driver,
        organization_id=organization_id
    )

    stats = loader.run()
    return stats.to_dict()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        auth=(
            os.environ.get("NEO4J_USER", "neo4j"),
            os.environ.get("NEO4J_PASSWORD", "password")
        )
    )

    try:
        print("Loading curated neurological disease associations...")
        stats = load_neurological_diseases(driver, organization_id="default")
        print("\nNeurological Diseases Loading Complete!")
        print(f"  Nodes created: {stats['nodes_created']}")
        print(f"  Relationships created: {stats['relationships_created']}")
        print(f"  Duration: {stats['duration_seconds']:.1f} seconds")
    finally:
        driver.close()
