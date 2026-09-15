#!/usr/bin/env python3
"""
Knowledge Graph Data Loading Orchestrator

This script handles downloading and loading all required data into the
Neo4j Knowledge Graph for the Graphomics platform.

Data sources:
1. NCBI Taxonomy - Microbial taxonomy hierarchy (~1M nodes)
2. Disbiome - Disease-microbiome associations
3. HMDB - Human Metabolome Database (requires XML download)
4. KEGG - Metabolic pathways (API-based)
5. DrugBank - Drug-target interactions (requires academic license)
6. ChEMBL - Bioactivity data (API-based, open-access)
7. PubChem - Compound data (API-based, public domain)
8. CARD - Antibiotic resistance database (requires data files)
9. Curated PRODUCES - Taxon-metabolite production relationships
10. Curated Neurological - Neurological disease-microbiome associations

Usage:
    python load_knowledge_graph.py --all              # Load all data
    python load_knowledge_graph.py --taxonomy         # Load taxonomy only
    python load_knowledge_graph.py --disbiome         # Load Disbiome only
    python load_knowledge_graph.py --produces         # Load PRODUCES only
    python load_knowledge_graph.py --hmdb             # Load HMDB metabolites
    python load_knowledge_graph.py --kegg             # Load KEGG pathways
    python load_knowledge_graph.py --drugbank         # Load DrugBank drugs
    python load_knowledge_graph.py --chembl           # Load ChEMBL bioactivity
    python load_knowledge_graph.py --pubchem          # Load PubChem compounds
    python load_knowledge_graph.py --card             # Load CARD resistance data
    python load_knowledge_graph.py --dgidb            # Load DGIdb drug-gene interactions
    python load_knowledge_graph.py --gutmdisorder     # Load gutMDisorder data
    python load_knowledge_graph.py --gmrepo           # Load GMrepo data
    python load_knowledge_graph.py --bugsigdb         # Load BugSigDB signatures
    python load_knowledge_graph.py --pubmed           # Load PubMed literature
    python load_knowledge_graph.py --semmeddb         # Load SemMedDB relationships
    python load_knowledge_graph.py --mbodymap         # Load mBodyMap body-site data
    python load_knowledge_graph.py --reactome         # Load Reactome pathway data
    python load_knowledge_graph.py --indexes          # Create indexes only
    python load_knowledge_graph.py --validate         # Validate data quality
"""

import os
import sys
import logging
import argparse
import tarfile
import json
from pathlib import Path
from datetime import datetime, timezone

import requests
from neo4j import GraphDatabase

from database.derive_spine import derive
from database.ingestion.hmdb_loader import backfill_scfa_flags
from database.migrate_curated_compounds import migrate as migrate_curated_compounds
from database.neo4j_schema import create_provenance_spine_schema

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Default settings
DEFAULT_NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
DEFAULT_NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "password")
DEFAULT_NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE", "graphomics")  # Changed from "neo4j"
DEFAULT_ORG_ID = "default"

# Data directory
DATA_DIR = Path(__file__).parent.parent / "data" / "knowledge_graph"


def ensure_data_dir():
    """Create data directory if it doesn't exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def get_neo4j_driver(uri=None, user=None, password=None):
    """Create Neo4j driver connection."""
    uri = uri or DEFAULT_NEO4J_URI
    user = user or DEFAULT_NEO4J_USER
    password = password or DEFAULT_NEO4J_PASSWORD

    driver = GraphDatabase.driver(uri, auth=(user, password))
    driver.verify_connectivity()
    logger.info(f"Connected to Neo4j at {uri}")
    return driver


def create_s3_manager(bucket: str, prefix: str = "data/"):
    """
    Create an S3DataManager if boto3 is available and a bucket is configured.

    Returns None if S3 is not available or not configured.
    """
    if not bucket:
        return None

    try:
        from database.ingestion.s3_data_manager import S3DataManager
        manager = S3DataManager(bucket=bucket, prefix=prefix)
        return manager
    except ImportError:
        logger.warning(
            "boto3 is not installed. S3 data management is unavailable. "
            "Install with: pip install boto3"
        )
        return None
    except Exception as e:
        logger.warning(f"Failed to initialize S3DataManager: {e}")
        return None


def upload_data_to_s3(data_dir: str, s3_manager) -> None:
    """
    Scan a local directory for known data files and upload them to S3.

    Args:
        data_dir: Path to local directory containing data files
        s3_manager: Initialized S3DataManager instance
    """
    # Map of expected file names to their S3 key
    known_files = {
        "hmdb_metabolites.xml": "hmdb/hmdb_metabolites.xml",
        "hmdb_metabolites.xml.gz": "hmdb/hmdb_metabolites.xml.gz",
        "drugbank_all_full_database.xml": "drugbank/drugbank.xml",
        "drugbank_all_full_database.xml.gz": "drugbank/drugbank.xml.gz",
        "drugbank.xml": "drugbank/drugbank.xml",
        "card.json": "card/card.json",
        "gutMDisorder.tsv": "gutmdisorder/gutmdisorder.tsv",
        "gutMDisorder.csv": "gutmdisorder/gutmdisorder.tsv",
        "bugsigdb.tsv": "bugsigdb/bugsigdb.csv",
        "bugsigdb.csv": "bugsigdb/bugsigdb.csv",
        "semmeddb_predications.tsv": "semmeddb/semmeddb_predications.tsv",
        "semmeddb_predications.csv": "semmeddb/semmeddb_predications.tsv",
        "mbodymap.tsv": "mbodymap/mbodymap.tsv",
        "mbodymap.csv": "mbodymap/mbodymap.tsv",
    }

    data_path = Path(data_dir)
    if not data_path.is_dir():
        logger.error(f"Data directory does not exist: {data_dir}")
        return

    uploaded = 0
    for root, _dirs, files in os.walk(data_path):
        for filename in files:
            if filename in known_files:
                local_file = os.path.join(root, filename)
                s3_key = known_files[filename]
                try:
                    s3_manager.upload(local_file, s3_key)
                    uploaded += 1
                except Exception as e:
                    logger.exception("Failed to upload %s: %s", local_file, e)

    if uploaded == 0:
        logger.warning(
            f"No known data files found in {data_dir}. "
            f"Expected files: {', '.join(sorted(set(known_files.values())))}"
        )
    else:
        logger.info(f"Uploaded {uploaded} file(s) to S3")


# Uniqueness constraints on the real merge key of each core entity (#272).
#
# Until #272 this function created NONE, despite its name — kgdev carried only
# the 3 provenance constraints (Analysis/Assertion/Experiment), which come from
# create_provenance_spine_schema, a different path. So nothing at the schema
# level objected to the duplicate :Compound identities behind #267/#271/#281.
#
# Each key is COMPOSITE on organization_id. Live has 4 orgs (default, graphomics,
# intrinsic-eval, demo); a bare `Taxon.taxon_id` is unique today only because all
# reference data is `default`, and would block a tenant loading its own copy
# tomorrow. The pre-existing Disease index is already
# (organization_id, name_normalized) — that is the intended key.
#
# Keys are what the loaders actually MERGE on, verified against live:
#   Compound  -> compound_id     (NOT metabolite_id: 0 nodes carry it since #146)
#   Gene      -> name            (derive_spine keys on name; all 545 gene_id NULL)
#   Disease   -> name_normalized (all 7 loaders MERGE on it; disease_id often
#                                 null — 11 of 464 — so constraining it would
#                                 fire spuriously. See neo4j_schema notes / #44)
#
# Neo4j ignores nodes missing any property in the key, so the 15 compounds with a
# null compound_id (3 twin-less curated + 12 from the #269 audit write) and the
# 232 drugs with a null drug_id are simply out of scope — the constraint still
# catches duplicates among nodes that HAVE the key.
#
# Analysis/Assertion/Experiment are deliberately absent: they already have bare
# `id` constraints from #257 and legitimately span orgs.
# Indexes that a uniqueness constraint now supersedes. A constraint brings its
# own backing index on the same key, and Neo4j REFUSES to create the constraint
# while a plain index on that key exists:
#
#   There already exists an index (:Disease {organization_id, name_normalized}).
#   A constraint cannot be created until the index has been dropped.
#
# This function used to create disease_org_name_normalized itself, so the
# Disease constraint would have failed on every run. Dropped before the
# constraints are attempted; the constraint's own index serves the same
# lookups, so nothing is lost. (#272)
SUPERSEDED_INDEXES = [
    "DROP INDEX disease_org_name_normalized IF EXISTS",
]

UNIQUENESS_CONSTRAINTS = [
    "CREATE CONSTRAINT taxon_org_id_unique IF NOT EXISTS "
    "FOR (n:Taxon) REQUIRE (n.organization_id, n.taxon_id) IS UNIQUE",
    "CREATE CONSTRAINT compound_org_id_unique IF NOT EXISTS "
    "FOR (n:Compound) REQUIRE (n.organization_id, n.compound_id) IS UNIQUE",
    "CREATE CONSTRAINT disease_org_normalized_unique IF NOT EXISTS "
    "FOR (n:Disease) REQUIRE (n.organization_id, n.name_normalized) IS UNIQUE",
    "CREATE CONSTRAINT paper_org_pmid_unique IF NOT EXISTS "
    "FOR (n:Paper) REQUIRE (n.organization_id, n.pmid) IS UNIQUE",
    "CREATE CONSTRAINT drug_org_id_unique IF NOT EXISTS "
    "FOR (n:Drug) REQUIRE (n.organization_id, n.drug_id) IS UNIQUE",
    "CREATE CONSTRAINT pathway_org_id_unique IF NOT EXISTS "
    "FOR (n:Pathway) REQUIRE (n.organization_id, n.pathway_id) IS UNIQUE",
    "CREATE CONSTRAINT protein_org_id_unique IF NOT EXISTS "
    "FOR (n:Protein) REQUIRE (n.organization_id, n.protein_id) IS UNIQUE",
    "CREATE CONSTRAINT gene_org_name_unique IF NOT EXISTS "
    "FOR (n:Gene) REQUIRE (n.organization_id, n.name) IS UNIQUE",
    "CREATE CONSTRAINT drugclass_org_name_unique IF NOT EXISTS "
    "FOR (n:DrugClass) REQUIRE (n.organization_id, n.name) IS UNIQUE",
    # #311. Keyed on the composite the MapForge submit path actually MERGEs on
    # (contribution.py `_build_contribution_merge`), NOT on `id`: the issue
    # asked for :Contribution(id), but ContributionRecord has no id field and
    # no writer sets one, so that constraint would bind to zero nodes forever —
    # the #298/#304 vacuous-guardrail shape. Genuinely org-composite: two
    # tenants may submit the same source file under the same mapping.
    # :Decision is deliberately NOT here — it is bare `id`, with its #257
    # siblings in create_provenance_spine_schema. See the note there.
    "CREATE CONSTRAINT contribution_org_sha_unique IF NOT EXISTS "
    "FOR (n:Contribution) REQUIRE "
    "(n.organization_id, n.mapping_sha256, n.source_sha256) IS UNIQUE",
    # #310. Cross-run lineage :Artifact keyed on its content hash, org-scoped so
    # two tenants that touch the same file get distinct nodes. sha256 is the
    # identity; the same content under different URIs is one artifact per org.
    "CREATE CONSTRAINT artifact_org_sha_unique IF NOT EXISTS "
    "FOR (n:Artifact) REQUIRE (n.organization_id, n.sha256) IS UNIQUE",
]


def create_indexes_and_constraints(driver, database: str = DEFAULT_NEO4J_DATABASE):
    """Create all required indexes and constraints for the knowledge graph.

    Returns a report of what was created. Constraint failures are logged at
    ERROR and counted rather than swallowed: a constraint that fails because
    duplicates already exist is precisely when you need to hear about it, and
    the load continuing quietly is how #272 persisted.
    """
    logger.info(f"Creating indexes and constraints on database '{database}'...")

    indexes = [
        # Disease indexes - important for normalized name matching
        "CREATE INDEX disease_normalized IF NOT EXISTS FOR (d:Disease) ON (d.name_normalized)",
        "CREATE INDEX disease_name IF NOT EXISTS FOR (d:Disease) ON (d.name)",
        "CREATE INDEX disease_sources IF NOT EXISTS FOR (d:Disease) ON (d.sources)",

        # Taxon indexes
        "CREATE INDEX taxon_id IF NOT EXISTS FOR (t:Taxon) ON (t.taxon_id)",
        "CREATE INDEX taxon_ncbi IF NOT EXISTS FOR (t:Taxon) ON (t.ncbi_tax_id)",
        "CREATE INDEX taxon_name IF NOT EXISTS FOR (t:Taxon) ON (t.name)",
        "CREATE INDEX taxon_rank IF NOT EXISTS FOR (t:Taxon) ON (t.rank)",
        "CREATE INDEX taxon_genus IF NOT EXISTS FOR (t:Taxon) ON (t.genus)",

        # Compound (metabolite) indexes — canonical label is :Compound since #146.
        # compound_id is the canonical key; metabolite_id is indexed too because
        # legacy-keyed nodes and API fallback lookups still reference it.
        "CREATE INDEX compound_id IF NOT EXISTS FOR (m:Compound) ON (m.compound_id)",
        "CREATE INDEX compound_metabolite_id IF NOT EXISTS FOR (m:Compound) ON (m.metabolite_id)",
        "CREATE INDEX compound_hmdb IF NOT EXISTS FOR (m:Compound) ON (m.hmdb_id)",
        "CREATE INDEX compound_name IF NOT EXISTS FOR (m:Compound) ON (m.name)",
        "CREATE INDEX compound_name_lower IF NOT EXISTS FOR (m:Compound) ON (m.name_lower)",
        "CREATE INDEX compound_kegg IF NOT EXISTS FOR (m:Compound) ON (m.kegg_id)",

        # Pathway indexes
        "CREATE INDEX pathway_id IF NOT EXISTS FOR (p:Pathway) ON (p.pathway_id)",
        "CREATE INDEX pathway_kegg IF NOT EXISTS FOR (p:Pathway) ON (p.kegg_id)",
        "CREATE INDEX pathway_name IF NOT EXISTS FOR (p:Pathway) ON (p.name)",

        # Protein indexes
        "CREATE INDEX protein_id IF NOT EXISTS FOR (p:Protein) ON (p.protein_id)",
        "CREATE INDEX protein_uniprot IF NOT EXISTS FOR (p:Protein) ON (p.uniprot_id)",

        # Drug indexes
        "CREATE INDEX drug_id IF NOT EXISTS FOR (d:Drug) ON (d.drug_id)",
        "CREATE INDEX drug_name IF NOT EXISTS FOR (d:Drug) ON (d.name)",

        # BodySite indexes
        "CREATE INDEX bodysite_id IF NOT EXISTS FOR (bs:BodySite) ON (bs.body_site_id)",
        "CREATE INDEX bodysite_name IF NOT EXISTS FOR (bs:BodySite) ON (bs.name_normalized)",

        # Paper indexes
        "CREATE INDEX paper_id IF NOT EXISTS FOR (p:Paper) ON (p.paper_id)",
        "CREATE INDEX paper_pmid IF NOT EXISTS FOR (p:Paper) ON (p.pmid)",

        # Study indexes
        "CREATE INDEX study_id IF NOT EXISTS FOR (s:Study) ON (s.study_id)",

        # Organization (multi-tenant) indexes
        "CREATE INDEX disease_org IF NOT EXISTS FOR (d:Disease) ON (d.organization_id)",
        "CREATE INDEX compound_org IF NOT EXISTS FOR (m:Compound) ON (m.organization_id)",

        # Gene indexes
        "CREATE INDEX gene_id IF NOT EXISTS FOR (g:Gene) ON (g.gene_id)",
        "CREATE INDEX gene_name IF NOT EXISTS FOR (g:Gene) ON (g.name)",
        "CREATE INDEX gene_symbol IF NOT EXISTS FOR (g:Gene) ON (g.symbol)",

        # Composite indexes for multi-tenant queries
        "CREATE INDEX taxon_org_name IF NOT EXISTS FOR (t:Taxon) ON (t.organization_id, t.name)",

        # Relationship property indexes for frequently filtered properties
        "CREATE INDEX rel_direction IF NOT EXISTS FOR ()-[r:ASSOCIATED_WITH_DISEASE]-() ON (r.direction)",
        "CREATE INDEX rel_evidence IF NOT EXISTS FOR ()-[r:ASSOCIATED_WITH_DISEASE]-() ON (r.evidence_level)",
        "CREATE INDEX rel_sources IF NOT EXISTS FOR ()-[r:ASSOCIATED_WITH_DISEASE]-() ON (r.sources)",
    ]

    # Full-text search indexes (separate list since they use different syntax)
    fulltext_indexes = [
        """CREATE FULLTEXT INDEX entity_fulltext IF NOT EXISTS
        FOR (n:Taxon|Disease|Metabolite|Drug|Gene|Pathway|Paper)
        ON EACH [n.name]""",
    ]

    constraints_created = 0
    constraints_failed = 0

    with driver.session(database=database) as session:
        for idx_query in indexes:
            try:
                session.run(idx_query)
                logger.debug(f"Created index: {idx_query[:60]}...")
            except Exception as e:
                logger.warning(f"Index creation warning: {e}")

        for ft_query in fulltext_indexes:
            try:
                session.run(ft_query)
                logger.debug(f"Created fulltext index: {ft_query[:60]}...")
            except Exception as e:
                logger.warning(f"Fulltext index creation warning: {e}")

        # Retire indexes a constraint now supersedes — must precede the
        # constraint, which Neo4j refuses while the plain index exists.
        for drop_query in SUPERSEDED_INDEXES:
            try:
                session.run(drop_query)
                logger.debug(f"Dropped superseded index: {drop_query}")
            except Exception as e:
                logger.warning(f"Superseded-index drop warning: {e}")

        # Uniqueness constraints on core entities (#272). Failures are ERRORs:
        # the usual cause is pre-existing duplicates, which is a data problem
        # worth stopping for, not a line to bury in the log.
        for con_query in UNIQUENESS_CONSTRAINTS:
            try:
                session.run(con_query)
                constraints_created += 1
                logger.debug(f"Created constraint: {con_query[:60]}...")
            except Exception as e:
                constraints_failed += 1
                # Deliberately not logger.exception: a uniqueness violation
                # is self-explanatory and the traceback is pure noise, but
                # it must still be loud — burying it is how #272 survived.
                logger.error(  # noqa: TRY400
                    "Constraint creation FAILED: %s -- %s. Usually means "
                    "duplicates already exist on that key; fix the data, then "
                    "re-run --indexes.",
                    con_query.split(" IF NOT EXISTS")[0], e,
                )

        # The spine creates the bare-`id` provenance constraints
        # (Experiment/Analysis/Assertion from #257, Decision from #311). Its
        # counts fold into the same totals: a caller gating a deploy on
        # constraints_failed must see every constraint failure, not only the
        # ones declared in UNIQUENESS_CONSTRAINTS.
        spine = create_provenance_spine_schema(session)
        constraints_created += spine["constraints_created"]
        constraints_failed += spine["constraints_failed"]

    logger.info(
        "Created %d indexes, %d fulltext indexes, %d uniqueness constraints "
        "(%d failed)",
        len(indexes), len(fulltext_indexes),
        constraints_created, constraints_failed,
    )
    return {
        "indexes": len(indexes),
        "fulltext_indexes": len(fulltext_indexes),
        "constraints_created": constraints_created,
        "constraints_failed": constraints_failed,
    }


def download_ncbi_taxonomy(data_dir: Path) -> Path:
    """Download NCBI Taxonomy dump files."""
    taxdump_dir = data_dir / "taxdump"
    tar_path = data_dir / "taxdump.tar.gz"

    # Check if already downloaded
    if (taxdump_dir / "nodes.dmp").exists():
        logger.info(f"NCBI Taxonomy already exists at {taxdump_dir}")
        return taxdump_dir

    url = "https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump.tar.gz"
    logger.info(f"Downloading NCBI Taxonomy from {url}")
    logger.info("This may take a few minutes (~50MB download)...")

    # 10s to establish TCP, 300s per chunk read — generous for the ~50MB
    # download but bounded so an NCBI FTP outage doesn't hang the loader
    # indefinitely.
    response = requests.get(url, stream=True, timeout=(10, 300))
    response.raise_for_status()

    total_size = int(response.headers.get('content-length', 0))
    downloaded = 0

    with open(tar_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
            downloaded += len(chunk)
            if total_size > 0:
                pct = (downloaded / total_size) * 100
                print(f"\rDownloading: {pct:.1f}%", end="", flush=True)
    print()

    logger.info(f"Extracting to {taxdump_dir}")
    taxdump_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r:gz") as tar:
        # filter='data' (PEP 706, available 3.11.4+) rejects absolute paths,
        # path traversal, and symlinks during extraction — defense-in-depth
        # in case the upstream tarball is ever compromised or replaced.
        tar.extractall(taxdump_dir, filter="data")

    # Clean up tar file
    tar_path.unlink()

    logger.info("NCBI Taxonomy download complete")
    return taxdump_dir


def create_sample_disbiome_data(data_dir: Path) -> Path:
    """
    Create a sample Disbiome dataset for testing.

    In production, you would download from https://disbiome.ugent.be
    or use their API to get the full dataset.
    """
    disbiome_file = data_dir / "disbiome_sample.json"

    if disbiome_file.exists():
        logger.info(f"Disbiome sample data exists at {disbiome_file}")
        return disbiome_file

    logger.info("Creating sample Disbiome data...")

    # Sample data based on real Disbiome associations
    sample_data = [
        # IBD associations
        {"organism_name": "Faecalibacterium prausnitzii", "organism_ncbi_id": "853", "disease_name": "Inflammatory Bowel Disease", "qualitative_outcome": "decreased", "sample_name": "stool", "method_name": "16S rRNA sequencing"},
        {"organism_name": "Akkermansia muciniphila", "organism_ncbi_id": "239935", "disease_name": "Inflammatory Bowel Disease", "qualitative_outcome": "decreased", "sample_name": "stool", "method_name": "16S rRNA sequencing"},
        {"organism_name": "Bacteroides fragilis", "organism_ncbi_id": "817", "disease_name": "Inflammatory Bowel Disease", "qualitative_outcome": "altered", "sample_name": "stool", "method_name": "16S rRNA sequencing"},
        {"organism_name": "Escherichia coli", "organism_ncbi_id": "562", "disease_name": "Inflammatory Bowel Disease", "qualitative_outcome": "increased", "sample_name": "stool", "method_name": "16S rRNA sequencing"},
        {"organism_name": "Roseburia intestinalis", "organism_ncbi_id": "166486", "disease_name": "Inflammatory Bowel Disease", "qualitative_outcome": "decreased", "sample_name": "stool", "method_name": "16S rRNA sequencing"},

        # Crohn's Disease
        {"organism_name": "Faecalibacterium prausnitzii", "organism_ncbi_id": "853", "disease_name": "Crohn's Disease", "qualitative_outcome": "decreased", "sample_name": "stool", "method_name": "16S rRNA sequencing"},
        {"organism_name": "Bacteroides vulgatus", "organism_ncbi_id": "821", "disease_name": "Crohn's Disease", "qualitative_outcome": "increased", "sample_name": "stool", "method_name": "16S rRNA sequencing"},
        {"organism_name": "Escherichia coli", "organism_ncbi_id": "562", "disease_name": "Crohn's Disease", "qualitative_outcome": "increased", "sample_name": "stool", "method_name": "metagenomics"},

        # Ulcerative Colitis
        {"organism_name": "Akkermansia muciniphila", "organism_ncbi_id": "239935", "disease_name": "Ulcerative Colitis", "qualitative_outcome": "decreased", "sample_name": "stool", "method_name": "16S rRNA sequencing"},
        {"organism_name": "Clostridium difficile", "organism_ncbi_id": "1496", "disease_name": "Ulcerative Colitis", "qualitative_outcome": "increased", "sample_name": "stool", "method_name": "culture"},

        # Obesity
        {"organism_name": "Akkermansia muciniphila", "organism_ncbi_id": "239935", "disease_name": "Obesity", "qualitative_outcome": "decreased", "sample_name": "stool", "method_name": "16S rRNA sequencing"},
        {"organism_name": "Bacteroides thetaiotaomicron", "organism_ncbi_id": "818", "disease_name": "Obesity", "qualitative_outcome": "increased", "sample_name": "stool", "method_name": "16S rRNA sequencing"},
        {"organism_name": "Firmicutes", "organism_ncbi_id": "1239", "disease_name": "Obesity", "qualitative_outcome": "increased", "sample_name": "stool", "method_name": "16S rRNA sequencing"},
        {"organism_name": "Bacteroidetes", "organism_ncbi_id": "976", "disease_name": "Obesity", "qualitative_outcome": "decreased", "sample_name": "stool", "method_name": "16S rRNA sequencing"},

        # Type 2 Diabetes
        {"organism_name": "Akkermansia muciniphila", "organism_ncbi_id": "239935", "disease_name": "Type 2 Diabetes", "qualitative_outcome": "decreased", "sample_name": "stool", "method_name": "metagenomics"},
        {"organism_name": "Faecalibacterium prausnitzii", "organism_ncbi_id": "853", "disease_name": "Type 2 Diabetes", "qualitative_outcome": "decreased", "sample_name": "stool", "method_name": "metagenomics"},
        {"organism_name": "Lactobacillus", "organism_ncbi_id": "1578", "disease_name": "Type 2 Diabetes", "qualitative_outcome": "decreased", "sample_name": "stool", "method_name": "16S rRNA sequencing"},

        # Colorectal Cancer
        {"organism_name": "Fusobacterium nucleatum", "organism_ncbi_id": "851", "disease_name": "Colorectal Cancer", "qualitative_outcome": "increased", "sample_name": "tumor tissue", "method_name": "16S rRNA sequencing"},
        {"organism_name": "Bacteroides fragilis", "organism_ncbi_id": "817", "disease_name": "Colorectal Cancer", "qualitative_outcome": "increased", "sample_name": "stool", "method_name": "metagenomics"},
        {"organism_name": "Peptostreptococcus", "organism_ncbi_id": "1257", "disease_name": "Colorectal Cancer", "qualitative_outcome": "increased", "sample_name": "stool", "method_name": "16S rRNA sequencing"},

        # Parkinson's Disease
        {"organism_name": "Akkermansia muciniphila", "organism_ncbi_id": "239935", "disease_name": "Parkinson's Disease", "qualitative_outcome": "increased", "sample_name": "stool", "method_name": "16S rRNA sequencing"},
        {"organism_name": "Prevotella", "organism_ncbi_id": "838", "disease_name": "Parkinson's Disease", "qualitative_outcome": "decreased", "sample_name": "stool", "method_name": "16S rRNA sequencing"},
        {"organism_name": "Enterobacteriaceae", "organism_ncbi_id": "543", "disease_name": "Parkinson's Disease", "qualitative_outcome": "increased", "sample_name": "stool", "method_name": "16S rRNA sequencing"},
        {"organism_name": "Lactobacillus", "organism_ncbi_id": "1578", "disease_name": "Parkinson's Disease", "qualitative_outcome": "decreased", "sample_name": "stool", "method_name": "metagenomics"},

        # Autism Spectrum Disorder
        {"organism_name": "Clostridium", "organism_ncbi_id": "1485", "disease_name": "Autism Spectrum Disorder", "qualitative_outcome": "increased", "sample_name": "stool", "method_name": "16S rRNA sequencing"},
        {"organism_name": "Desulfovibrio", "organism_ncbi_id": "872", "disease_name": "Autism Spectrum Disorder", "qualitative_outcome": "increased", "sample_name": "stool", "method_name": "culture"},
        {"organism_name": "Bifidobacterium", "organism_ncbi_id": "1678", "disease_name": "Autism Spectrum Disorder", "qualitative_outcome": "decreased", "sample_name": "stool", "method_name": "16S rRNA sequencing"},

        # Depression
        {"organism_name": "Lactobacillus", "organism_ncbi_id": "1578", "disease_name": "Depression", "qualitative_outcome": "decreased", "sample_name": "stool", "method_name": "16S rRNA sequencing"},
        {"organism_name": "Bifidobacterium", "organism_ncbi_id": "1678", "disease_name": "Depression", "qualitative_outcome": "decreased", "sample_name": "stool", "method_name": "metagenomics"},
        {"organism_name": "Coprococcus", "organism_ncbi_id": "33042", "disease_name": "Depression", "qualitative_outcome": "decreased", "sample_name": "stool", "method_name": "metagenomics"},

        # Alzheimer's Disease
        {"organism_name": "Firmicutes", "organism_ncbi_id": "1239", "disease_name": "Alzheimer's Disease", "qualitative_outcome": "decreased", "sample_name": "stool", "method_name": "16S rRNA sequencing"},
        {"organism_name": "Bacteroidetes", "organism_ncbi_id": "976", "disease_name": "Alzheimer's Disease", "qualitative_outcome": "increased", "sample_name": "stool", "method_name": "16S rRNA sequencing"},
        {"organism_name": "Escherichia coli", "organism_ncbi_id": "562", "disease_name": "Alzheimer's Disease", "qualitative_outcome": "increased", "sample_name": "stool", "method_name": "metagenomics"},

        # Rheumatoid Arthritis
        {"organism_name": "Prevotella copri", "organism_ncbi_id": "165179", "disease_name": "Rheumatoid Arthritis", "qualitative_outcome": "increased", "sample_name": "stool", "method_name": "metagenomics"},
        {"organism_name": "Lactobacillus", "organism_ncbi_id": "1578", "disease_name": "Rheumatoid Arthritis", "qualitative_outcome": "decreased", "sample_name": "stool", "method_name": "16S rRNA sequencing"},

        # Multiple Sclerosis
        {"organism_name": "Akkermansia muciniphila", "organism_ncbi_id": "239935", "disease_name": "Multiple Sclerosis", "qualitative_outcome": "increased", "sample_name": "stool", "method_name": "16S rRNA sequencing"},
        {"organism_name": "Prevotella", "organism_ncbi_id": "838", "disease_name": "Multiple Sclerosis", "qualitative_outcome": "decreased", "sample_name": "stool", "method_name": "metagenomics"},

        # Celiac Disease
        {"organism_name": "Bifidobacterium", "organism_ncbi_id": "1678", "disease_name": "Celiac Disease", "qualitative_outcome": "decreased", "sample_name": "duodenal biopsy", "method_name": "16S rRNA sequencing"},
        {"organism_name": "Bacteroides", "organism_ncbi_id": "816", "disease_name": "Celiac Disease", "qualitative_outcome": "increased", "sample_name": "stool", "method_name": "16S rRNA sequencing"},

        # Liver Disease / NAFLD
        {"organism_name": "Escherichia coli", "organism_ncbi_id": "562", "disease_name": "Non-Alcoholic Fatty Liver Disease", "qualitative_outcome": "increased", "sample_name": "stool", "method_name": "metagenomics"},
        {"organism_name": "Bacteroides", "organism_ncbi_id": "816", "disease_name": "Non-Alcoholic Fatty Liver Disease", "qualitative_outcome": "altered", "sample_name": "stool", "method_name": "16S rRNA sequencing"},

        # Irritable Bowel Syndrome
        {"organism_name": "Lactobacillus", "organism_ncbi_id": "1578", "disease_name": "Irritable Bowel Syndrome", "qualitative_outcome": "decreased", "sample_name": "stool", "method_name": "16S rRNA sequencing"},
        {"organism_name": "Veillonella", "organism_ncbi_id": "29465", "disease_name": "Irritable Bowel Syndrome", "qualitative_outcome": "increased", "sample_name": "stool", "method_name": "16S rRNA sequencing"},

        # Asthma
        {"organism_name": "Bifidobacterium", "organism_ncbi_id": "1678", "disease_name": "Asthma", "qualitative_outcome": "decreased", "sample_name": "stool", "method_name": "16S rRNA sequencing"},
        {"organism_name": "Clostridium difficile", "organism_ncbi_id": "1496", "disease_name": "Asthma", "qualitative_outcome": "increased", "sample_name": "stool", "method_name": "culture"},

        # Atopic Dermatitis
        {"organism_name": "Faecalibacterium prausnitzii", "organism_ncbi_id": "853", "disease_name": "Atopic Dermatitis", "qualitative_outcome": "decreased", "sample_name": "stool", "method_name": "16S rRNA sequencing"},
        {"organism_name": "Staphylococcus aureus", "organism_ncbi_id": "1280", "disease_name": "Atopic Dermatitis", "qualitative_outcome": "increased", "sample_name": "skin", "method_name": "culture"},
    ]

    with open(disbiome_file, 'w') as f:
        json.dump(sample_data, f, indent=2)

    logger.info(f"Created sample Disbiome data with {len(sample_data)} associations")
    return disbiome_file


def load_ncbi_taxonomy(driver, taxdump_dir: Path, organization_id: str = DEFAULT_ORG_ID, database: str = DEFAULT_NEO4J_DATABASE):
    """Load NCBI Taxonomy data into Neo4j."""
    logger.info("Loading NCBI Taxonomy data...")

    # Import here to avoid circular imports
    sys.path.insert(0, str(Path(__file__).parent))
    from ingestion.ncbi_taxonomy_loader import NCBITaxonomyLoader

    loader = NCBITaxonomyLoader(
        driver=driver,
        organization_id=organization_id,
        taxdump_dir=str(taxdump_dir),
        filter_to_microbiome=True,
        batch_size=5000,
        database=database
    )

    stats = loader.run()
    logger.info(f"NCBI Taxonomy loaded: {stats.to_dict()}")
    return stats


def load_disbiome_data(driver, disbiome_file: Path, organization_id: str = DEFAULT_ORG_ID, database: str = DEFAULT_NEO4J_DATABASE):
    """Load Disbiome disease-microbiome associations."""
    logger.info("Loading Disbiome data...")

    sys.path.insert(0, str(Path(__file__).parent))
    from ingestion.disbiome_loader import DisbiomeLoader

    loader = DisbiomeLoader(
        driver=driver,
        organization_id=organization_id,
        file_path=str(disbiome_file),
        batch_size=500,
        database=database
    )

    stats = loader.run()
    logger.info(f"Disbiome data loaded: {stats.to_dict()}")
    return stats


def load_produces_relationships(driver, organization_id: str = DEFAULT_ORG_ID, database: str = DEFAULT_NEO4J_DATABASE):
    """Load curated Taxon-Metabolite PRODUCES relationships."""
    logger.info("Loading PRODUCES relationships...")

    sys.path.insert(0, str(Path(__file__).parent))
    from ingestion.produces_loader import ProducesLoader

    loader = ProducesLoader(
        driver=driver,
        organization_id=organization_id,
        batch_size=100,
        database=database
    )

    stats = loader.run()
    logger.info(f"PRODUCES relationships loaded: {stats.to_dict()}")
    return stats


def load_neurological_data(driver, organization_id: str = DEFAULT_ORG_ID, database: str = DEFAULT_NEO4J_DATABASE):
    """Load curated neurological disease-microbiome associations."""
    logger.info("Loading neurological disease data...")

    sys.path.insert(0, str(Path(__file__).parent))
    from ingestion.neurological_diseases_loader import NeurologicalDiseasesLoader

    loader = NeurologicalDiseasesLoader(
        driver=driver,
        organization_id=organization_id,
        batch_size=100,
        database=database
    )

    stats = loader.run()
    logger.info(f"Neurological disease data loaded: {stats.to_dict()}")
    return stats


def load_hmdb_data(driver, organization_id: str = DEFAULT_ORG_ID, database: str = DEFAULT_NEO4J_DATABASE, s3_manager=None):
    """Load HMDB metabolite data into Neo4j."""
    s3_local_path = None
    try:
        xml_file = None

        if s3_manager:
            try:
                s3_local_path = s3_manager.download("hmdb/hmdb_metabolites.xml")
                xml_file = s3_local_path
            except Exception as e:
                logger.warning(f"S3 download failed for HMDB, falling back to local: {e}")

        if not xml_file:
            hmdb_path = os.environ.get("HMDB_DATA_PATH", str(DATA_DIR / "hmdb"))
            # Look for HMDB XML file (plain or gzipped)
            for candidate in [
                Path(hmdb_path) / "hmdb_metabolites.xml.gz",
                Path(hmdb_path) / "hmdb_metabolites.xml",
                Path(hmdb_path),  # direct file path
            ]:
                if candidate.is_file():
                    xml_file = str(candidate)
                    break

        if not xml_file:
            logger.warning(
                "HMDB data not found. "
                "Download from https://hmdb.ca/system/downloads/current/hmdb_metabolites.zip "
                "and extract to data/knowledge_graph/hmdb/"
            )
            return None

        logger.info(f"Loading HMDB data from {xml_file}...")
        sys.path.insert(0, str(Path(__file__).parent))
        from ingestion.hmdb_loader import HMDBLoader

        loader = HMDBLoader(
            driver=driver,
            organization_id=organization_id,
            file_path=xml_file,
            batch_size=500,
            database=database,
            filter_to_microbial=True
        )

        stats = loader.run()
        logger.info(f"HMDB data loaded: {stats.to_dict()}")
        return stats
    finally:
        if s3_manager and s3_local_path:
            s3_manager.cleanup(s3_local_path)


def load_kegg_data(driver, organization_id: str = DEFAULT_ORG_ID, database: str = DEFAULT_NEO4J_DATABASE):
    """Load KEGG pathway data into Neo4j (API-based)."""
    logger.info("Loading KEGG pathway data (this may take a while due to API rate limits)...")

    sys.path.insert(0, str(Path(__file__).parent))
    from ingestion.kegg_loader import KEGGLoader

    loader = KEGGLoader(
        driver=driver,
        organization_id=organization_id,
        batch_size=100,
        database=database
    )

    stats = loader.run()
    logger.info(f"KEGG data loaded: {stats.to_dict()}")
    return stats


def load_drugbank_data(driver, organization_id: str = DEFAULT_ORG_ID, database: str = DEFAULT_NEO4J_DATABASE, s3_manager=None):
    """Load DrugBank drug-target data into Neo4j."""
    s3_local_path = None
    try:
        xml_file = None

        if s3_manager:
            try:
                s3_local_path = s3_manager.download("drugbank/drugbank.xml")
                xml_file = s3_local_path
            except Exception as e:
                logger.warning(f"S3 download failed for DrugBank, falling back to local: {e}")

        if not xml_file:
            drugbank_path = os.environ.get("DRUGBANK_DATA_PATH", str(DATA_DIR / "drugbank"))
            for candidate in [
                Path(drugbank_path) / "drugbank_all_full_database.xml.gz",
                Path(drugbank_path) / "drugbank_all_full_database.xml",
                Path(drugbank_path),
            ]:
                if candidate.is_file():
                    xml_file = str(candidate)
                    break

        if not xml_file:
            logger.warning(
                "DrugBank data not found. "
                "Download from https://go.drugbank.com/releases (requires academic license) "
                "and place in data/knowledge_graph/drugbank/"
            )
            return None

        logger.info(f"Loading DrugBank data from {xml_file}...")
        sys.path.insert(0, str(Path(__file__).parent))
        from ingestion.drugbank_loader import DrugBankLoader

        loader = DrugBankLoader(
            driver=driver,
            organization_id=organization_id,
            file_path=xml_file,
            batch_size=100,
            database=database
        )

        stats = loader.run()
        logger.info(f"DrugBank data loaded: {stats.to_dict()}")
        return stats
    finally:
        if s3_manager and s3_local_path:
            s3_manager.cleanup(s3_local_path)


def load_chembl_data(driver, organization_id: str = DEFAULT_ORG_ID, database: str = DEFAULT_NEO4J_DATABASE):
    """Load ChEMBL drug-target data into Neo4j (API-based)."""
    logger.info("Loading ChEMBL data (API-based, this may take a while)...")

    sys.path.insert(0, str(Path(__file__).parent))
    from ingestion.chembl_loader import ChEMBLLoader

    loader = ChEMBLLoader(
        driver=driver,
        organization_id=organization_id,
        batch_size=500,
        database=database,
        include_clinical_candidates=True
    )

    stats = loader.run()
    logger.info(f"ChEMBL data loaded: {stats.to_dict()}")
    return stats


def load_pubchem_data(driver, organization_id: str = DEFAULT_ORG_ID, database: str = DEFAULT_NEO4J_DATABASE):
    """Load PubChem compound data into Neo4j (API-based, microbiome-relevant compounds)."""
    logger.info("Loading PubChem data (microbiome-relevant compounds)...")

    sys.path.insert(0, str(Path(__file__).parent))
    from ingestion.pubchem_loader import load_microbiome_related_compounds

    stats = load_microbiome_related_compounds(driver, organization_id=organization_id)
    logger.info(f"PubChem data loaded: {stats}")
    return stats


def load_dgidb_data(driver, organization_id: str = DEFAULT_ORG_ID, database: str = DEFAULT_NEO4J_DATABASE):
    """Load DGIdb drug-gene interaction data into Neo4j (API-based, CC BY 4.0)."""
    logger.info("Loading DGIdb data (API-based, this may take a while)...")

    sys.path.insert(0, str(Path(__file__).parent))
    from ingestion.dgidb_loader import DGIdbLoader

    loader = DGIdbLoader(
        driver=driver,
        organization_id=organization_id,
        database=database,
    )

    stats = loader.run()
    logger.info(f"DGIdb data loaded: {stats.to_dict()}")
    return stats


def load_card_data(driver, organization_id: str = DEFAULT_ORG_ID, database: str = DEFAULT_NEO4J_DATABASE, s3_manager=None):
    """Load CARD antibiotic resistance data into Neo4j."""
    s3_local_path = None
    try:
        card_path = None

        if s3_manager:
            try:
                s3_local_path = s3_manager.download("card/card.json")
                # CARDLoader expects a directory; derive it from the downloaded file
                card_path = str(Path(s3_local_path).parent)
            except Exception as e:
                logger.warning(f"S3 download failed for CARD, falling back to local: {e}")

        if not card_path:
            card_path = os.environ.get("CARD_DATA_PATH", str(DATA_DIR / "card"))
            card_dir = Path(card_path)
            if not card_dir.is_dir() or not (card_dir / "card.json").exists():
                logger.warning(
                    "CARD data not found. "
                    "Download from https://card.mcmaster.ca/download "
                    "and extract to data/knowledge_graph/card/"
                )
                return None

        logger.info(f"Loading CARD data from {card_path}...")
        sys.path.insert(0, str(Path(__file__).parent))
        from ingestion.card_loader import CARDLoader

        loader = CARDLoader(
            driver=driver,
            organization_id=organization_id,
            card_data_dir=card_path,
            batch_size=500,
            database=database
        )

        stats = loader.run()
        logger.info(f"CARD data loaded: {stats.to_dict()}")
        return stats
    finally:
        if s3_manager and s3_local_path:
            s3_manager.cleanup(s3_local_path)


def load_gutmdisorder_data(driver, organization_id: str = DEFAULT_ORG_ID, database: str = DEFAULT_NEO4J_DATABASE, s3_manager=None):
    """Load gutMDisorder gut microbiota-disorder associations."""
    s3_local_path = None
    try:
        data_file = None

        if s3_manager:
            try:
                s3_local_path = s3_manager.download("gutmdisorder/gutmdisorder.tsv")
                data_file = s3_local_path
            except Exception as e:
                logger.warning(f"S3 download failed for gutMDisorder, falling back to local: {e}")

        if not data_file:
            data_path = os.environ.get("GUTMDISORDER_DATA_PATH", str(DATA_DIR / "gutmdisorder"))
            for candidate in [
                Path(data_path) / "gutMDisorder.tsv",
                Path(data_path) / "gutMDisorder.csv",
                Path(data_path),
            ]:
                if candidate.is_file():
                    data_file = str(candidate)
                    break

        if not data_file:
            logger.warning(
                "gutMDisorder data not found. "
                "Download from http://bio-annotation.cn/gutMDisorder/ "
                "and place in data/knowledge_graph/gutmdisorder/"
            )
            return None

        logger.info(f"Loading gutMDisorder data from {data_file}...")
        sys.path.insert(0, str(Path(__file__).parent))
        from ingestion.gutmdisorder_loader import GutMDisorderLoader

        loader = GutMDisorderLoader(
            driver=driver,
            organization_id=organization_id,
            file_path=data_file,
            batch_size=500,
            database=database
        )

        stats = loader.run()
        logger.info(f"gutMDisorder data loaded: {stats.to_dict()}")
        return stats
    finally:
        if s3_manager and s3_local_path:
            s3_manager.cleanup(s3_local_path)


def load_gmrepo_data(driver, organization_id: str = DEFAULT_ORG_ID, database: str = DEFAULT_NEO4J_DATABASE):
    """Load GMrepo human gut metagenome data (API-based)."""
    logger.info("Loading GMrepo data (API-based, this may take a while)...")

    sys.path.insert(0, str(Path(__file__).parent))
    from ingestion.gmrepo_loader import GMrepoLoader

    loader = GMrepoLoader(
        driver=driver,
        organization_id=organization_id,
        batch_size=500,
        database=database
    )

    stats = loader.run()
    logger.info(f"GMrepo data loaded: {stats.to_dict()}")
    return stats


def load_bugsigdb_data(driver, organization_id: str = DEFAULT_ORG_ID, database: str = DEFAULT_NEO4J_DATABASE, s3_manager=None):
    """Load BugSigDB microbiome signatures."""
    s3_local_path = None
    try:
        data_file = None

        if s3_manager:
            try:
                s3_local_path = s3_manager.download("bugsigdb/bugsigdb.csv")
                data_file = s3_local_path
            except Exception as e:
                logger.warning(f"S3 download failed for BugSigDB, falling back to local: {e}")

        if not data_file:
            data_path = os.environ.get("BUGSIGDB_DATA_PATH", str(DATA_DIR / "bugsigdb"))
            for candidate in [
                Path(data_path) / "bugsigdb.tsv",
                Path(data_path) / "bugsigdb.csv",
                Path(data_path),
            ]:
                if candidate.is_file():
                    data_file = str(candidate)
                    break

        if not data_file:
            logger.warning(
                "BugSigDB data not found. "
                "Download from https://bugsigdb.org/ "
                "and place in data/knowledge_graph/bugsigdb/"
            )
            return None

        logger.info(f"Loading BugSigDB data from {data_file}...")
        sys.path.insert(0, str(Path(__file__).parent))
        from ingestion.bugsigdb_loader import BugSigDBLoader

        loader = BugSigDBLoader(
            driver=driver,
            organization_id=organization_id,
            file_path=data_file,
            batch_size=500,
            database=database
        )

        stats = loader.run()
        logger.info(f"BugSigDB data loaded: {stats.to_dict()}")
        return stats
    finally:
        if s3_manager and s3_local_path:
            s3_manager.cleanup(s3_local_path)


def load_pubmed_data(driver, organization_id: str = DEFAULT_ORG_ID, database: str = DEFAULT_NEO4J_DATABASE):
    """Load PubMed/MEDLINE biomedical literature (API-based)."""
    logger.info("Loading PubMed data (API-based)...")

    sys.path.insert(0, str(Path(__file__).parent))
    from ingestion.pubmed_loader import PubMedLoader

    loader = PubMedLoader(
        driver=driver,
        organization_id=organization_id,
        batch_size=100,
        database=database
    )

    stats = loader.run()
    logger.info(f"PubMed data loaded: {stats.to_dict()}")
    return stats


def load_semmeddb_data(driver, organization_id: str = DEFAULT_ORG_ID, database: str = DEFAULT_NEO4J_DATABASE, s3_manager=None):
    """Load SemMedDB literature-derived relationships."""
    s3_local_path = None
    try:
        data_file = None

        if s3_manager:
            try:
                s3_local_path = s3_manager.download("semmeddb/semmeddb_predications.tsv")
                data_file = s3_local_path
            except Exception as e:
                logger.warning(f"S3 download failed for SemMedDB, falling back to local: {e}")

        if not data_file:
            data_path = os.environ.get("SEMMEDDB_DATA_PATH", str(DATA_DIR / "semmeddb"))
            for candidate in [
                Path(data_path) / "semmeddb_predications.tsv",
                Path(data_path) / "semmeddb_predications.csv",
                Path(data_path),
            ]:
                if candidate.is_file():
                    data_file = str(candidate)
                    break

        if not data_file:
            logger.warning(
                "SemMedDB data not found. "
                "Download from https://lhncbc.nlm.nih.gov/ii/tools/SemRep_SemMedDB_SKR/SemMedDB_download.html "
                "and place in data/knowledge_graph/semmeddb/"
            )
            return None

        logger.info(f"Loading SemMedDB data from {data_file}...")
        sys.path.insert(0, str(Path(__file__).parent))
        from ingestion.semmeddb_loader import SemMedDBLoader

        loader = SemMedDBLoader(
            driver=driver,
            organization_id=organization_id,
            file_path=data_file,
            batch_size=1000,
            database=database
        )

        stats = loader.run()
        logger.info(f"SemMedDB data loaded: {stats.to_dict()}")
        return stats
    finally:
        if s3_manager and s3_local_path:
            s3_manager.cleanup(s3_local_path)


def load_mbodymap_data(driver, organization_id: str = DEFAULT_ORG_ID, database: str = DEFAULT_NEO4J_DATABASE, s3_manager=None):
    """Load mBodyMap body-site microbiome reference data."""
    s3_local_path = None
    try:
        data_file = None

        if s3_manager:
            try:
                s3_local_path = s3_manager.download("mbodymap/mbodymap.tsv")
                data_file = s3_local_path
            except Exception as e:
                logger.warning(f"S3 download failed for mBodyMap, falling back to local: {e}")

        if not data_file:
            data_path = os.environ.get("MBODYMAP_DATA_PATH", str(DATA_DIR / "mbodymap"))
            for candidate in [
                Path(data_path) / "mbodymap.tsv",
                Path(data_path) / "mbodymap.csv",
                Path(data_path),
            ]:
                if candidate.is_file():
                    data_file = str(candidate)
                    break

        if not data_file:
            logger.warning(
                "mBodyMap data not found. "
                "Download from https://mbodymap.microbiome.cloud/ "
                "and place in data/knowledge_graph/mbodymap/"
            )
            return None

        logger.info(f"Loading mBodyMap data from {data_file}...")
        sys.path.insert(0, str(Path(__file__).parent))
        from ingestion.mbodymap_loader import MBodyMapLoader

        loader = MBodyMapLoader(
            driver=driver,
            organization_id=organization_id,
            file_path=data_file,
            batch_size=500,
            database=database
        )

        stats = loader.run()
        logger.info(f"mBodyMap data loaded: {stats.to_dict()}")
        return stats
    finally:
        if s3_manager and s3_local_path:
            s3_manager.cleanup(s3_local_path)


def load_reactome_data(driver, organization_id: str = DEFAULT_ORG_ID, database: str = DEFAULT_NEO4J_DATABASE):
    """Load Reactome pathway data into Neo4j (API-based)."""
    logger.info("Loading Reactome pathway data (this may take a while due to API rate limits)...")

    sys.path.insert(0, str(Path(__file__).parent))
    from ingestion.reactome_loader import ReactomeLoader

    loader = ReactomeLoader(
        driver=driver,
        organization_id=organization_id,
        batch_size=100,
        database=database
    )

    stats = loader.run()
    logger.info(f"Reactome data loaded: {stats.to_dict()}")
    return stats


# ---------------------------------------------------------------------------
# Metabolomics discipline loaders
# ---------------------------------------------------------------------------

def load_hmdb_metabolomics_data(
    driver,
    organization_id: str = DEFAULT_ORG_ID,
    database: str = DEFAULT_NEO4J_DATABASE,
    s3_manager=None,
):
    """Load HMDB human endogenous metabolite data (metabolomics discipline)."""
    s3_local_path = None
    try:
        data_file = None

        if s3_manager:
            try:
                s3_local_path = s3_manager.download("hmdb/hmdb_metabolomics.xml")
                data_file = s3_local_path
            except Exception as e:
                logger.warning(f"S3 download failed for HMDB Metabolomics, falling back to local: {e}")

        if not data_file:
            data_path = os.environ.get("HMDB_METABOLOMICS_DATA_PATH", str(DATA_DIR / "hmdb"))
            for candidate in [
                Path(data_path) / "hmdb_metabolites.xml.gz",
                Path(data_path) / "hmdb_metabolites.xml",
                Path(data_path),
            ]:
                if candidate.is_file():
                    data_file = str(candidate)
                    break

        if not data_file:
            logger.warning(
                "HMDB Metabolomics data not found. "
                "Download from https://hmdb.ca/system/downloads/current/hmdb_metabolites.zip "
                "and extract to data/knowledge_graph/hmdb/"
            )
            return None

        logger.info(f"Loading HMDB Metabolomics data from {data_file}...")
        sys.path.insert(0, str(Path(__file__).parent))
        from ingestion.hmdb_metabolomics_loader import HMDBMetabolomicsLoader

        loader = HMDBMetabolomicsLoader(
            driver=driver,
            organization_id=organization_id,
            file_path=data_file,
            batch_size=500,
            database=database,
        )

        stats = loader.run()
        logger.info(f"HMDB Metabolomics data loaded: {stats.to_dict()}")
        return stats
    finally:
        if s3_manager and s3_local_path:
            s3_manager.cleanup(s3_local_path)


def load_metacyc_data(
    driver,
    organization_id: str = DEFAULT_ORG_ID,
    database: str = DEFAULT_NEO4J_DATABASE,
):
    """Load MetaCyc compounds and pathways (metabolomics discipline)."""
    data_path = os.environ.get("METACYC_DATA_PATH", str(DATA_DIR / "metacyc"))
    metacyc_dir = Path(data_path)

    if not metacyc_dir.is_dir():
        logger.warning(
            "MetaCyc data not found. "
            "Download from https://metacyc.org/downloads.shtml "
            "and extract to data/knowledge_graph/metacyc/"
        )
        return None

    logger.info(f"Loading MetaCyc data from {metacyc_dir}...")
    sys.path.insert(0, str(Path(__file__).parent))
    from ingestion.metacyc_loader import MetaCycLoader

    loader = MetaCycLoader(
        driver=driver,
        organization_id=organization_id,
        file_path=str(metacyc_dir),
        batch_size=500,
        database=database,
    )

    stats = loader.run()
    logger.info(f"MetaCyc data loaded: {stats.to_dict()}")
    return stats


def load_metabo_lights_data(
    driver,
    organization_id: str = DEFAULT_ORG_ID,
    database: str = DEFAULT_NEO4J_DATABASE,
):
    """Load MetaboLights metabolomics study data (metabolomics discipline)."""
    data_path = os.environ.get("METABO_LIGHTS_DATA_PATH", str(DATA_DIR / "metabo_lights"))
    path = Path(data_path)

    if not path.exists():
        logger.warning(
            "MetaboLights data not found. "
            "Download study JSON files from https://www.ebi.ac.uk/metabolights/ "
            "and place in data/knowledge_graph/metabo_lights/"
        )
        return None

    logger.info(f"Loading MetaboLights data from {path}...")
    sys.path.insert(0, str(Path(__file__).parent))
    from ingestion.metabo_lights_loader import MetaboLightsLoader

    loader = MetaboLightsLoader(
        driver=driver,
        organization_id=organization_id,
        file_path=str(path),
        batch_size=500,
        database=database,
    )

    stats = loader.run()
    logger.info(f"MetaboLights data loaded: {stats.to_dict()}")
    return stats


def load_uniprot_proteomics_data(driver, organization_id: str = DEFAULT_ORG_ID, database: str = DEFAULT_NEO4J_DATABASE):
    """Load UniProtKB proteins + PTMs + disease/pathway edges (proteomics discipline)."""
    sys.path.insert(0, str(Path(__file__).parent))
    from ingestion.uniprot_proteomics_loader import UniProtProteomicsLoader
    data_path = os.environ.get("UNIPROT_PROTEOMICS_DATA_PATH", "")
    if not data_path or not Path(data_path).exists():
        logger.warning("UNIPROT_PROTEOMICS_DATA_PATH not set or not found — skipping UniProt proteomics load")
        return None
    loader = UniProtProteomicsLoader(driver=driver, organization_id=organization_id,
                                     file_path=data_path, database=database)
    stats = loader.run()
    logger.info(f"UniProt proteomics data loaded: {stats.to_dict()}")
    return stats


def load_phosphosite_data(driver, organization_id: str = DEFAULT_ORG_ID, database: str = DEFAULT_NEO4J_DATABASE):
    """Load PhosphoSitePlus modification sites (proteomics discipline)."""
    sys.path.insert(0, str(Path(__file__).parent))
    from ingestion.phosphosite_loader import PhosphoSiteLoader
    data_path = os.environ.get("PHOSPHOSITE_DATA_PATH", "")
    if not data_path or not Path(data_path).exists():
        logger.warning("PHOSPHOSITE_DATA_PATH not set or not found — skipping PhosphoSite load")
        return None
    loader = PhosphoSiteLoader(driver=driver, organization_id=organization_id,
                               data_dir=data_path, database=database)
    stats = loader.run()
    logger.info(f"PhosphoSite data loaded: {stats.to_dict()}")
    return stats


def load_pride_data(driver, organization_id: str = DEFAULT_ORG_ID, database: str = DEFAULT_NEO4J_DATABASE):
    """Load PRIDE archive proteomics studies + measurements (proteomics discipline)."""
    sys.path.insert(0, str(Path(__file__).parent))
    from ingestion.pride_loader import PRIDELoader
    data_path = os.environ.get("PRIDE_DATA_PATH", "")
    if not data_path or not Path(data_path).exists():
        logger.warning("PRIDE_DATA_PATH not set or not found — skipping PRIDE load")
        return None
    loader = PRIDELoader(driver=driver, organization_id=organization_id,
                         data_dir=data_path, database=database)
    stats = loader.run()
    logger.info(f"PRIDE data loaded: {stats.to_dict()}")
    return stats


#: #345: an ASSOCIATED_WITH_DISEASE edge is "source-tracked" if it carries
#: EITHER `r.sources` (plural list, set by ingestion loaders — Disbiome/GMRepo)
#: OR `r.source` (singular, set by the provenance spine = 'provenance-spine',
#: incl. the #297B parallel per-org projections). The old check tested only
#: `r.sources IS NULL`, so it flagged every spine-projected edge as untracked.
REL_SOURCE_TRACKING_QUERY = """
    MATCH ()-[r:ASSOCIATED_WITH_DISEASE]->()
    WHERE r.sources IS NULL AND r.source IS NULL
    RETURN count(r) as count
"""


def validate_knowledge_graph(driver, database: str = DEFAULT_NEO4J_DATABASE):
    """Run validation queries to check data quality."""
    logger.info("Validating knowledge graph data quality...")

    validations = []

    with driver.session(database=database) as session:
        # 1. Check for duplicate diseases (case-insensitive)
        result = session.run("""
            MATCH (d:Disease)
            WITH d.name_normalized as norm, count(*) as cnt
            WHERE cnt > 1
            RETURN norm, cnt
        """)
        duplicates = list(result)
        validations.append({
            "check": "Duplicate diseases (case-insensitive)",
            "passed": len(duplicates) == 0,
            "details": f"Found {len(duplicates)} duplicates" if duplicates else "No duplicates"
        })

        # 2. Check diseases have name_normalized
        result = session.run("""
            MATCH (d:Disease) WHERE d.name_normalized IS NULL
            RETURN count(d) as count
        """)
        count = result.single()["count"]
        validations.append({
            "check": "Diseases have name_normalized",
            "passed": count == 0,
            "details": f"{count} diseases missing name_normalized" if count else "All diseases normalized"
        })

        # 3. Check Taxon-Disease associations exist
        result = session.run("""
            MATCH (t:Taxon)-[r:ASSOCIATED_WITH_DISEASE]->(d:Disease)
            RETURN count(r) as count
        """)
        count = result.single()["count"]
        validations.append({
            "check": "Taxon-Disease associations",
            "passed": count > 0,
            "details": f"{count} associations found"
        })

        # 4. Check relationships have source tracking (r.source OR r.sources, #345)
        result = session.run(REL_SOURCE_TRACKING_QUERY)
        count = result.single()["count"]
        validations.append({
            "check": "Relationships have source tracking",
            "passed": count == 0,
            "details": f"{count} relationships missing source tracking" if count else "All relationships tracked"
        })

        # 5. Node counts
        result = session.run("""
            MATCH (n)
            WITH labels(n)[0] as label, count(*) as count
            RETURN label, count ORDER BY count DESC
        """)
        node_counts = {r["label"]: r["count"] for r in result}
        validations.append({
            "check": "Node counts",
            "passed": True,
            "details": node_counts
        })

        # 6. Relationship counts
        result = session.run("""
            MATCH ()-[r]->()
            WITH type(r) as rel_type, count(*) as count
            RETURN rel_type, count ORDER BY count DESC
        """)
        rel_counts = {r["rel_type"]: r["count"] for r in result}
        validations.append({
            "check": "Relationship counts",
            "passed": True,
            "details": rel_counts
        })

    # Print results
    print("\n" + "=" * 60)
    print("KNOWLEDGE GRAPH VALIDATION RESULTS")
    print("=" * 60)

    all_passed = True
    for v in validations:
        status = "[PASS]" if v["passed"] else "[FAIL]"
        all_passed = all_passed and v["passed"]
        print(f"\n{status}: {v['check']}")
        if isinstance(v["details"], dict):
            for k, val in v["details"].items():
                print(f"   {k}: {val}")
        else:
            print(f"   {v['details']}")

    print("\n" + "=" * 60)
    print(f"OVERALL: {'ALL CHECKS PASSED' if all_passed else 'SOME CHECKS FAILED'}")
    print("=" * 60 + "\n")

    return validations


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser.

    Extracted from main() so it can be reused in tests without calling parse_args().
    """
    parser = argparse.ArgumentParser(description="Load Knowledge Graph data into Neo4j")

    parser.add_argument("--all", action="store_true", help="Load all data")
    parser.add_argument("--indexes", action="store_true", help="Create indexes only")
    parser.add_argument("--taxonomy", action="store_true", help="Load NCBI Taxonomy")
    parser.add_argument("--disbiome", action="store_true", help="Load Disbiome data")
    parser.add_argument("--produces", action="store_true", help="Load PRODUCES relationships")
    parser.add_argument("--neurological", action="store_true", help="Load neurological disease data")
    parser.add_argument("--hmdb", action="store_true", help="Load HMDB metabolite data")
    parser.add_argument("--kegg", action="store_true", help="Load KEGG pathway data")
    parser.add_argument("--drugbank", action="store_true", help="Load DrugBank drug-target data")
    parser.add_argument("--chembl", action="store_true", help="Load ChEMBL bioactivity data")
    parser.add_argument("--pubchem", action="store_true", help="Load PubChem compound data")
    parser.add_argument("--card", action="store_true", help="Load CARD antibiotic resistance data")
    parser.add_argument("--dgidb", action="store_true", help="Load DGIdb drug-gene interaction data")
    parser.add_argument("--gutmdisorder", action="store_true", help="Load gutMDisorder data")
    parser.add_argument("--gmrepo", action="store_true", help="Load GMrepo gut metagenome data")
    parser.add_argument("--bugsigdb", action="store_true", help="Load BugSigDB signatures")
    parser.add_argument("--pubmed", action="store_true", help="Load PubMed literature data")
    parser.add_argument("--semmeddb", action="store_true", help="Load SemMedDB relationships")
    parser.add_argument("--mbodymap", action="store_true", help="Load mBodyMap body-site data")
    parser.add_argument("--reactome", action="store_true", help="Load Reactome pathway data")
    parser.add_argument("--derive", action="store_true",
                        help="Derive the pathway->disease spine (IMPLICATED_IN) + "
                             "genes (ENCODED_BY) from the loaded graph (additive, idempotent)")
    parser.add_argument("--migrate-curated-compounds", action="store_true",
                        help="Move curated PRODUCES onto canonical HMDB compounds and "
                             "retire the legacy name_lower nodes (#281). DESTRUCTIVE: "
                             "dry-run unless --apply is also given")
    parser.add_argument("--migrate-duplicate-diseases", action="store_true",
                        help="Merge duplicate :Disease node pairs (apostrophe/"
                             "possessive/plural variants) onto the genera-richest "
                             "canonical, preserving aliases (#267). DESTRUCTIVE: "
                             "dry-run unless --apply is also given")
    parser.add_argument("--apply", action="store_true",
                        help="Actually perform --migrate-curated-compounds / "
                             "--migrate-duplicate-diseases (without this they only "
                             "report what they would do)")
    parser.add_argument("--backfill-scfa", action="store_true",
                        help="Clear stale is_scfa/carbon_chain_length left by the "
                             "#277 substring bug (repair step, idempotent)")
    parser.add_argument("--backfill-disbiome-papers", action="store_true",
                        help="Backfill paper_id/source/created_at on the 125 "
                             "out-of-band Disbiome :Paper nodes (#269 Step 2, "
                             "idempotent)")
    # Metabolomics discipline flags
    parser.add_argument("--metabolomics-hmdb", action="store_true", help="Load HMDB metabolomics data (metabolomics discipline)")
    parser.add_argument("--metacyc", action="store_true", help="Load MetaCyc compound/pathway data")
    parser.add_argument("--metabo-lights", action="store_true", help="Load MetaboLights study data")
    parser.add_argument("--metabolomics", action="store_true", help="Load all metabolomics discipline data (HMDB metabolomics + MetaCyc + MetaboLights)")
    # Proteomics discipline flags
    parser.add_argument("--uniprot-proteomics", action="store_true",
                        help="Load UniProtKB proteins + PTMs + disease/pathway edges (proteomics discipline)")
    parser.add_argument("--phosphosite", action="store_true",
                        help="Load PhosphoSitePlus modification sites (proteomics discipline)")
    parser.add_argument("--pride", action="store_true",
                        help="Load PRIDE archive proteomics studies + measurements (proteomics discipline)")
    parser.add_argument("--proteomics", action="store_true",
                        help="Load all proteomics discipline data (UniProt + PhosphoSite + PRIDE) in order")
    parser.add_argument("--validate", action="store_true", help="Validate data quality")
    parser.add_argument("--validate-xrefs", action="store_true", help="Run cross-reference validation across data sources")
    parser.add_argument("--deduplicate", action="store_true", help="Run entity deduplication")

    parser.add_argument("--neo4j-uri", default=DEFAULT_NEO4J_URI, help="Neo4j URI")
    parser.add_argument("--neo4j-user", default=DEFAULT_NEO4J_USER, help="Neo4j user")
    parser.add_argument("--neo4j-password", default=DEFAULT_NEO4J_PASSWORD, help="Neo4j password")
    parser.add_argument("--neo4j-database", default=DEFAULT_NEO4J_DATABASE, help="Neo4j database name")
    parser.add_argument("--org-id", default=DEFAULT_ORG_ID, help="Organization ID")

    # S3 data management
    parser.add_argument(
        "--s3-bucket",
        default=os.environ.get("S3_DATA_BUCKET", ""),
        help="S3 bucket for data files (env: S3_DATA_BUCKET)"
    )
    parser.add_argument(
        "--s3-prefix",
        default=os.environ.get("S3_DATA_PREFIX", "data/"),
        help="S3 key prefix for data files (env: S3_DATA_PREFIX)"
    )
    parser.add_argument(
        "--upload-data",
        metavar="DIR",
        help="Upload local data files from DIR to S3 (requires --s3-bucket)"
    )

    return parser


#: Every flag that names a specific action. If NONE is set, main() defaults to
#: --all. The standalone maintenance ops (--derive, --migrate-*, --backfill-*)
#: MUST be here: without them a bare `--migrate-duplicate-diseases` (etc.) left
#: this set empty and silently flipped on --all, running the whole load pipeline
#: (taxonomy, sample disbiome, produces, ...) under a maintenance command — which
#: hung a t3.medium and re-created data the migration had just cleaned up.
EXPLICIT_ACTION_FLAGS = (
    "all", "indexes", "taxonomy", "disbiome", "produces", "neurological", "hmdb",
    "kegg", "drugbank", "chembl", "pubchem", "card", "dgidb", "gutmdisorder", "gmrepo",
    "bugsigdb", "pubmed", "semmeddb", "mbodymap", "reactome", "metabolomics_hmdb",
    "metacyc", "metabo_lights", "metabolomics", "uniprot_proteomics", "phosphosite",
    "pride", "proteomics", "validate", "validate_xrefs", "deduplicate", "derive",
    "migrate_curated_compounds", "migrate_duplicate_diseases", "backfill_scfa",
    "backfill_disbiome_papers",
)


def has_explicit_action(args: argparse.Namespace) -> bool:
    """True if any specific action flag is set. `getattr(..., False)` so a flag
    missing from the Namespace defaults to off rather than re-arming the --all
    footgun by raising."""
    return any(getattr(args, name, False) for name in EXPLICIT_ACTION_FLAGS)


def _dispatch(args: argparse.Namespace, driver, *, s3_manager=None) -> None:
    """Execute the loader pipeline described by *args* using *driver*.

    Extracted from main() so tests can call it directly without spawning a
    subprocess or connecting to a real Neo4j instance.
    """
    data_dir = ensure_data_dir()

    # Create indexes
    if args.all or args.indexes:
        create_indexes_and_constraints(driver, args.neo4j_database)

    # Load taxonomy
    if args.all or args.taxonomy:
        taxdump_dir = download_ncbi_taxonomy(data_dir)
        load_ncbi_taxonomy(driver, taxdump_dir, args.org_id, args.neo4j_database)

    # Load Disbiome
    if args.all or args.disbiome:
        disbiome_file = create_sample_disbiome_data(data_dir)
        load_disbiome_data(driver, disbiome_file, args.org_id, args.neo4j_database)

    # Load PRODUCES relationships
    if args.all or args.produces:
        load_produces_relationships(driver, args.org_id, args.neo4j_database)

    # Load neurological disease data
    if args.all or args.neurological:
        load_neurological_data(driver, args.org_id, args.neo4j_database)

    # Load HMDB metabolite data
    if args.all or args.hmdb:
        load_hmdb_data(driver, args.org_id, args.neo4j_database, s3_manager=s3_manager)

    # Load KEGG pathway data
    if args.all or args.kegg:
        load_kegg_data(driver, args.org_id, args.neo4j_database)

    # Load DrugBank data
    if args.all or args.drugbank:
        load_drugbank_data(driver, args.org_id, args.neo4j_database, s3_manager=s3_manager)

    # Load ChEMBL data
    if args.all or args.chembl:
        load_chembl_data(driver, args.org_id, args.neo4j_database)

    # Load PubChem data
    if args.all or args.pubchem:
        load_pubchem_data(driver, args.org_id, args.neo4j_database)

    # Load CARD data
    if args.all or args.card:
        load_card_data(driver, args.org_id, args.neo4j_database, s3_manager=s3_manager)

    # Load DGIdb data
    if args.all or args.dgidb:
        load_dgidb_data(driver, args.org_id, args.neo4j_database)

    # Load gutMDisorder data
    if args.all or args.gutmdisorder:
        load_gutmdisorder_data(driver, args.org_id, args.neo4j_database, s3_manager=s3_manager)

    # Load GMrepo data
    if args.all or args.gmrepo:
        load_gmrepo_data(driver, args.org_id, args.neo4j_database)

    # Load BugSigDB data
    if args.all or args.bugsigdb:
        load_bugsigdb_data(driver, args.org_id, args.neo4j_database, s3_manager=s3_manager)

    # Load PubMed data
    if args.all or args.pubmed:
        load_pubmed_data(driver, args.org_id, args.neo4j_database)

    # Load SemMedDB data
    if args.all or args.semmeddb:
        load_semmeddb_data(driver, args.org_id, args.neo4j_database, s3_manager=s3_manager)

    # Load mBodyMap data
    if args.all or args.mbodymap:
        load_mbodymap_data(driver, args.org_id, args.neo4j_database, s3_manager=s3_manager)

    # Load Reactome pathway data
    if args.all or args.reactome:
        load_reactome_data(driver, args.org_id, args.neo4j_database)

    # Metabolomics discipline loaders
    # --metabolomics-hmdb or --metabolomics or --all
    if args.all or args.metabolomics or args.metabolomics_hmdb:
        load_hmdb_metabolomics_data(driver, args.org_id, args.neo4j_database, s3_manager=s3_manager)

    # --metacyc or --metabolomics or --all
    if args.all or args.metabolomics or args.metacyc:
        load_metacyc_data(driver, args.org_id, args.neo4j_database)

    # --metabo-lights or --metabolomics or --all
    if args.all or args.metabolomics or args.metabo_lights:
        load_metabo_lights_data(driver, args.org_id, args.neo4j_database)

    # Proteomics discipline loaders
    # --uniprot-proteomics or --proteomics or --all
    if args.all or args.proteomics or args.uniprot_proteomics:
        load_uniprot_proteomics_data(driver, args.org_id, args.neo4j_database)

    # --phosphosite or --proteomics or --all
    if args.all or args.proteomics or args.phosphosite:
        load_phosphosite_data(driver, args.org_id, args.neo4j_database)

    # --pride or --proteomics or --all
    if args.all or args.proteomics or args.pride:
        load_pride_data(driver, args.org_id, args.neo4j_database)

    # Deduplicate (run after loading, before validation)
    if args.all or args.deduplicate:
        from database.deduplication import run_deduplication
        run_deduplication(driver, args.neo4j_database)

    # Derive the pathway->disease spine + genes (additive materialization over
    # the loaded graph; standalone, intentionally NOT part of --all).
    if args.derive:
        counts = derive(driver, args.neo4j_database)
        logger.info("Derived spine + genes: %s", counts)

    # Repair stale is_scfa flags (#277). Standalone like --derive: it fixes
    # existing data that a re-load cannot reach, so it must not ride along
    # with --all.
    # #281: retire the legacy curated compound nodes onto their canonical
    # twins. Destructive (moves edges, deletes nodes) and standalone —
    # never part of --all, and dry-run unless --apply is passed.
    if args.migrate_curated_compounds:
        report = migrate_curated_compounds(
            driver, args.neo4j_database, dry_run=not args.apply
        )
        logger.info("Curated-compound migration: %s", report)

    if args.migrate_duplicate_diseases:
        from database.migrate_duplicate_diseases import migrate as migrate_duplicate_diseases
        report = migrate_duplicate_diseases(
            driver, args.neo4j_database, dry_run=not args.apply
        )
        logger.info("Duplicate-disease merge: %s", report)

    if args.backfill_scfa:
        cleared = backfill_scfa_flags(driver, args.neo4j_database)
        logger.info("Cleared stale is_scfa flags: %d", cleared)

    if args.backfill_disbiome_papers:
        from database.ingestion.disbiome_loader import backfill_disbiome_paper_shape
        repaired = backfill_disbiome_paper_shape(driver, args.neo4j_database)
        logger.info("Backfilled Disbiome :Paper shape: %d", repaired)

    # Validate
    if args.all or args.validate:
        validate_knowledge_graph(driver, args.neo4j_database)

    # Cross-reference validation (also runs as part of --validate)
    if args.validate or args.validate_xrefs:
        from database.validation import run_cross_reference_validation
        report = run_cross_reference_validation(driver, args.neo4j_database)

        # Exit non-zero so --validate can gate a deploy and is scriptable.
        # Previously every outcome exited 0, so a broken graph was
        # indistinguishable from a clean one to anything but a human reading
        # the printed report (#269). Errors only — warnings stay advisory.
        # Note --all also validates but never exits here: aborting a long load
        # at the report step would be worse than reporting it.
        error_count = report["summary"]["errors"]
        if error_count:
            logger.error(
                "Validation failed with %d error(s) — see the report above.",
                error_count,
            )
            sys.exit(1)


def main():
    parser = _build_arg_parser()
    args = parser.parse_args()

    # Handle --upload-data separately (does not require Neo4j)
    if args.upload_data:
        if not args.s3_bucket:
            logger.error("--s3-bucket is required for --upload-data")
            sys.exit(1)
        s3_mgr = create_s3_manager(args.s3_bucket, args.s3_prefix)
        if not s3_mgr:
            logger.error("Failed to initialize S3. Is boto3 installed?")
            sys.exit(1)
        upload_data_to_s3(args.upload_data, s3_mgr)
        return

    # Default to --all only when the caller asked for nothing specific.
    if not has_explicit_action(args):
        args.all = True

    # Initialize S3 manager (None if not configured or boto3 unavailable)
    s3_manager = create_s3_manager(args.s3_bucket, args.s3_prefix)
    if s3_manager:
        logger.info(f"S3 data source enabled: s3://{args.s3_bucket}/{args.s3_prefix}")
    else:
        logger.info("S3 not configured, using local data files only")

    try:
        driver = get_neo4j_driver(args.neo4j_uri, args.neo4j_user, args.neo4j_password)
    except Exception as e:
        logger.exception("Failed to connect to Neo4j: %s", e)
        # Operator hint, not the error itself — keep at error level for visibility
        # but don't re-attach the (same) traceback that logger.exception above did.
        logger.error("Make sure Neo4j is running (docker-compose up neo4j)")  # noqa: TRY400
        sys.exit(1)

    try:
        start_time = datetime.now(timezone.utc)
        _dispatch(args, driver, s3_manager=s3_manager)
        elapsed = datetime.now(timezone.utc) - start_time
        logger.info(f"Total time: {elapsed}")

    finally:
        driver.close()


if __name__ == "__main__":
    main()
