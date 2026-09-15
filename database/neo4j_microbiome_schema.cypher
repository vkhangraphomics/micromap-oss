// ==============================================================================
// Graphomics Microbiome Knowledge Graph Schema
// ==============================================================================
// This file contains the complete Neo4j schema for microbiome data analysis.
//
// Designed for:
// - Multi-omic microbiome analysis
// - Clinical metadata integration
// - Literature linking
// - Multi-tenant SaaS architecture
//
// Author: Graphomics Platform Team
// Version: 1.0.0
// ==============================================================================

// ==============================================================================
// 1. CONSTRAINTS & UNIQUENESS
// ==============================================================================

// Organization (Multi-Tenancy)
CREATE CONSTRAINT org_id_unique IF NOT EXISTS
FOR (o:Organization) REQUIRE o.id IS UNIQUE;

// Taxon
CREATE CONSTRAINT taxon_ncbi_id_unique IF NOT EXISTS
FOR (t:Taxon) REQUIRE t.ncbi_id IS UNIQUE;

// Sample
CREATE CONSTRAINT sample_id_unique IF NOT EXISTS
FOR (s:Sample) REQUIRE s.id IS UNIQUE;

// Patient/Subject
CREATE CONSTRAINT patient_id_unique IF NOT EXISTS
FOR (p:Patient) REQUIRE p.id IS UNIQUE;

// Paper/Publication
CREATE CONSTRAINT paper_pmid_unique IF NOT EXISTS
FOR (p:Paper) REQUIRE p.pmid IS UNIQUE;

CREATE CONSTRAINT paper_doi_unique IF NOT EXISTS
FOR (p:Paper) REQUIRE p.doi IS UNIQUE;

// Gene
CREATE CONSTRAINT gene_id_unique IF NOT EXISTS
FOR (g:Gene) REQUIRE g.id IS UNIQUE;

// Pathway
CREATE CONSTRAINT pathway_id_unique IF NOT EXISTS
FOR (pw:Pathway) REQUIRE pw.id IS UNIQUE;

// Metabolite
CREATE CONSTRAINT metabolite_id_unique IF NOT EXISTS
FOR (m:Metabolite) REQUIRE m.id IS UNIQUE;

// Dataset/Study
CREATE CONSTRAINT dataset_id_unique IF NOT EXISTS
FOR (d:Dataset) REQUIRE d.id IS UNIQUE;

// ==============================================================================
// 2. INDEXES FOR PERFORMANCE
// ==============================================================================

// Taxon indexes
CREATE INDEX taxon_name IF NOT EXISTS
FOR (t:Taxon) ON (t.name);

CREATE INDEX taxon_rank IF NOT EXISTS
FOR (t:Taxon) ON (t.rank);

CREATE INDEX taxon_organization_id IF NOT EXISTS
FOR (t:Taxon) ON (t.organization_id);

// Sample indexes
CREATE INDEX sample_organization_id IF NOT EXISTS
FOR (s:Sample) ON (s.organization_id);

CREATE INDEX sample_patient_id IF NOT EXISTS
FOR (s:Sample) ON (s.patient_id);

CREATE INDEX sample_collection_date IF NOT EXISTS
FOR (s:Sample) ON (s.collection_date);

// Patient indexes
CREATE INDEX patient_organization_id IF NOT EXISTS
FOR (p:Patient) ON (p.organization_id);

CREATE INDEX patient_disease_state IF NOT EXISTS
FOR (p:Patient) ON (p.disease_state);

// Paper indexes
CREATE INDEX paper_year IF NOT EXISTS
FOR (p:Paper) ON (p.publication_year);

CREATE INDEX paper_title IF NOT EXISTS
FOR (p:Paper) ON (p.title);

// Full-text search indexes
CREATE FULLTEXT INDEX paper_search IF NOT EXISTS
FOR (p:Paper) ON EACH [p.title, p.abstract];

CREATE FULLTEXT INDEX taxon_search IF NOT EXISTS
FOR (t:Taxon) ON EACH [t.name, t.common_name];

// ==============================================================================
// 3. NODE LABELS & PROPERTIES
// ==============================================================================

// -----------------------------
// Organization (Multi-Tenancy)
// -----------------------------
// MERGE (o:Organization {
//     id: "org_startup_alpha",
//     name: "Startup Alpha Therapeutics",
//     tier: "growth",                    // starter, growth, pro
//     created_at: datetime(),
//     status: "active"
// });

// -----------------------------
// Taxon (Microbiome Organisms)
// -----------------------------
// CREATE (t:Taxon {
//     ncbi_id: "239935",                 // NCBI Taxonomy ID (unique)
//     name: "Akkermansia muciniphila",   // Scientific name
//     common_name: "Akkermansia",        // Common name (if any)
//     rank: "species",                   // domain, phylum, class, order, family, genus, species
//     lineage: ["Bacteria", "Verrucomicrobia", "Verrucomicrobiae", "Verrucomicrobiales", "Akkermansiaceae", "Akkermansia"],
//     parent_ncbi_id: "239934",          // Parent taxon
//     gram_stain: "negative",            // positive, negative, variable, unknown
//     oxygen_requirement: "anaerobic",   // aerobic, anaerobic, facultative
//     organization_id: "org_startup_alpha",
//     created_at: datetime(),
//     updated_at: datetime()
// });

// -----------------------------
// Sample (Microbiome Samples)
// -----------------------------
// CREATE (s:Sample {
//     id: "SMP001",                      // Unique sample ID
//     patient_id: "PAT001",              // Link to patient
//     organization_id: "org_startup_alpha",
//
//     // Collection metadata
//     collection_date: date("2025-01-15"),
//     collection_site: "gut",            // gut, skin, oral, vaginal, etc.
//     sample_type: "stool",
//
//     // Diversity metrics (calculated by agents)
//     shannon_diversity: 3.45,           // Alpha diversity
//     simpson_diversity: 0.92,
//     chao1_richness: 245,
//     observed_otus: 198,
//
//     // Sequencing metadata
//     sequencing_platform: "Illumina MiSeq",
//     sequencing_depth: 50000,
//     target_region: "V3-V4",
//
//     // Clinical context
//     disease_state: "healthy",          // healthy, diseased, treated, etc.
//     treatment_status: "pre_treatment",
//
//     // Quality control
//     qc_passed: true,
//     qc_flags: [],
//
//     created_at: datetime(),
//     processed_at: datetime()
// });

// -----------------------------
// Patient/Subject
// -----------------------------
// CREATE (p:Patient {
//     id: "PAT001",
//     organization_id: "org_startup_alpha",
//
//     // Demographics
//     age: 45,
//     sex: "F",                          // M, F, O
//     ethnicity: "caucasian",
//
//     // Clinical metadata
//     disease_state: "obese",            // healthy, obese, diabetic, IBD, etc.
//     bmi: 32.5,
//     diagnosis: "obesity",
//     diagnosis_date: date("2024-06-01"),
//
//     // Treatment history
//     current_medications: ["metformin"],
//     previous_treatments: [],
//
//     // Lifestyle
//     diet: "western",                   // western, mediterranean, vegan, etc.
//     exercise_frequency: "low",         // low, moderate, high
//
//     // Privacy
//     anonymized: true,
//     consent_date: date("2025-01-01"),
//
//     created_at: datetime(),
//     updated_at: datetime()
// });

// -----------------------------
// Paper/Publication
// -----------------------------
// CREATE (p:Paper {
//     pmid: "22797518",                  // PubMed ID (unique)
//     doi: "10.1073/pnas.1219451110",   // DOI (unique)
//     title: "Cross-talk between Akkermansia muciniphila and intestinal epithelium controls diet-induced obesity",
//     abstract: "Obesity and type 2 diabetes are characterized by altered gut microbiota...",
//
//     // Publication metadata
//     authors: ["Everard A", "Belzer C", "Geurts L", "..."],
//     journal: "Proceedings of the National Academy of Sciences",
//     publication_year: 2013,
//     publication_date: date("2013-05-28"),
//
//     // Citations
//     citation_count: 1234,
//
//     // Keywords/MeSH terms
//     keywords: ["obesity", "microbiome", "Akkermansia", "metabolism"],
//     mesh_terms: ["Obesity", "Gastrointestinal Microbiome", "Verrucomicrobia"],
//
//     // Full text (if available)
//     full_text_url: "https://...",
//     open_access: true,
//
//     created_at: datetime()
// });

// -----------------------------
// Gene
// -----------------------------
// CREATE (g:Gene {
//     id: "ENSG00000123456",             // Ensembl Gene ID or similar
//     symbol: "TNF",                     // Gene symbol
//     name: "Tumor Necrosis Factor",     // Full name
//     organism: "Homo sapiens",
//     chromosome: "6",
//     start_position: 31575565,
//     end_position: 31578336,
//     function: "Pro-inflammatory cytokine...",
//     created_at: datetime()
// });

// -----------------------------
// Pathway
// -----------------------------
// CREATE (pw:Pathway {
//     id: "KEGG:00010",                  // KEGG, Reactome, or GO ID
//     database: "KEGG",                  // KEGG, Reactome, GO, MetaCyc
//     name: "Glycolysis / Gluconeogenesis",
//     description: "The pathway that converts glucose to pyruvate...",
//     category: "metabolism",
//     created_at: datetime()
// });

// -----------------------------
// Metabolite
// -----------------------------
// CREATE (m:Metabolite {
//     id: "HMDB0000001",                 // HMDB ID
//     name: "1-Methylhistidine",
//     formula: "C7H11N3O2",
//     mass: 169.09,
//     class: "amino_acid_derivative",
//     kegg_id: "C01152",
//     created_at: datetime()
// });

// -----------------------------
// Dataset/Study
// -----------------------------
// CREATE (d:Dataset {
//     id: "DS001",
//     organization_id: "org_startup_alpha",
//     name: "Obesity Microbiome Study 2025",
//     description: "Longitudinal study of gut microbiome changes in obese patients...",
//     data_type: "16S_rRNA",             // 16S_rRNA, shotgun, metabolomics, etc.
//     sample_count: 120,
//     patient_count: 40,
//     timepoints: 3,
//     created_at: datetime(),
//     published: false
// });

// ==============================================================================
// 4. RELATIONSHIPS
// ==============================================================================

// -----------------------------
// Taxonomic Hierarchy
// -----------------------------
// (child:Taxon)-[:HAS_PARENT]->(t:Taxon)
//
// HAS_PARENT is the ONLY hierarchy edge (#298). Neither PARENT_OF nor CHILD_OF
// has ever been written; this file previously documented both, which is where
// the phantom CHILD_OF guardrails came from.

// Example:
// MATCH (parent:Taxon {ncbi_id: "239934"})  // Akkermansia genus
// MATCH (child:Taxon {ncbi_id: "239935"})   // A. muciniphila
// CREATE (child)-[:HAS_PARENT]->(parent);

// -----------------------------
// Sample Composition
// -----------------------------
// (s:Sample)-[:CONTAINS {
//     abundance: 0.05,                   // Relative abundance (0-1)
//     reads: 2500,                       // Read count
//     rank: 3,                           // Abundance rank (1 = most abundant)
//     detection_method: "16S_rRNA"
// }]->(t:Taxon)

// -----------------------------
// Patient Samples
// -----------------------------
// (p:Patient)-[:HAS_SAMPLE {
//     timepoint: 0,                      // 0 = baseline, 1 = followup, etc.
//     collection_date: date("2025-01-15")
// }]->(s:Sample)

// -----------------------------
// Organization Ownership
// -----------------------------
// (o:Organization)-[:OWNS]->(p:Patient)
// (o:Organization)-[:OWNS]->(s:Sample)
// (o:Organization)-[:OWNS]->(d:Dataset)

// -----------------------------
// Dataset Membership
// -----------------------------
// (d:Dataset)-[:INCLUDES]->(s:Sample)
// (d:Dataset)-[:INCLUDES]->(p:Patient)

// -----------------------------
// Literature Links
// -----------------------------
// (t:Taxon)-[:MENTIONED_IN {
//     relevance_score: 0.95,             // 0-1, from NLP analysis
//     context: "obesity",
//     mention_count: 15,
//     first_mentioned: 2013
// }]->(p:Paper)

// (p:Paper)-[:CITES]->(cited:Paper)
// (p:Paper)-[:CITED_BY]->(citing:Paper)

// -----------------------------
// Gene-Taxon Relationships
// -----------------------------
// (t:Taxon)-[:ENCODES]->(g:Gene)
// (t:Taxon)-[:HAS_GENE]->(g:Gene)

// -----------------------------
// Pathway Relationships
// -----------------------------
// (t:Taxon)-[:PARTICIPATES_IN]->(pw:Pathway)
// (g:Gene)-[:PART_OF_PATHWAY]->(pw:Pathway)
// (m:Metabolite)-[:INVOLVED_IN]->(pw:Pathway)

// -----------------------------
// Clinical Associations
// -----------------------------
// (t:Taxon)-[:ASSOCIATED_WITH {
//     effect: "beneficial",              // beneficial, harmful, neutral
//     evidence_level: "strong",          // strong, moderate, weak
//     condition: "obesity",
//     direction: "negative_correlation"  // positive, negative, no_correlation
// }]->(p:Patient {disease_state: "obese"})

// ==============================================================================
// 5. SAMPLE DATA (for testing)
// ==============================================================================

// Create sample organization
MERGE (org:Organization {
    id: "org_demo",
    name: "Demo Organization",
    tier: "starter",
    created_at: datetime(),
    status: "active"
});

// Create sample taxa
MERGE (t1:Taxon {
    ncbi_id: "239935",
    name: "Akkermansia muciniphila",
    common_name: "Akkermansia",
    rank: "species",
    lineage: ["Bacteria", "Verrucomicrobia", "Verrucomicrobiae", "Verrucomicrobiales", "Akkermansiaceae", "Akkermansia", "Akkermansia muciniphila"],
    gram_stain: "negative",
    oxygen_requirement: "anaerobic",
    organization_id: "org_demo",
    created_at: datetime()
});

MERGE (t2:Taxon {
    ncbi_id: "1680",
    name: "Bifidobacterium adolescentis",
    common_name: "Bifidobacterium",
    rank: "species",
    lineage: ["Bacteria", "Actinobacteria", "Actinobacteria", "Bifidobacteriales", "Bifidobacteriaceae", "Bifidobacterium", "Bifidobacterium adolescentis"],
    gram_stain: "positive",
    oxygen_requirement: "anaerobic",
    organization_id: "org_demo",
    created_at: datetime()
});

// Create sample patient
MERGE (pat:Patient {
    id: "PAT_DEMO_001",
    organization_id: "org_demo",
    age: 45,
    sex: "F",
    ethnicity: "caucasian",
    disease_state: "healthy",
    bmi: 24.5,
    diet: "mediterranean",
    exercise_frequency: "moderate",
    anonymized: true,
    created_at: datetime()
});

// Create sample microbiome sample
MERGE (smp:Sample {
    id: "SMP_DEMO_001",
    patient_id: "PAT_DEMO_001",
    organization_id: "org_demo",
    collection_date: date("2025-01-15"),
    collection_site: "gut",
    sample_type: "stool",
    shannon_diversity: 3.45,
    simpson_diversity: 0.92,
    chao1_richness: 245,
    observed_otus: 198,
    sequencing_platform: "Illumina MiSeq",
    disease_state: "healthy",
    qc_passed: true,
    created_at: datetime()
});

// Link patient to sample
MATCH (p:Patient {id: "PAT_DEMO_001"})
MATCH (s:Sample {id: "SMP_DEMO_001"})
MERGE (p)-[:HAS_SAMPLE {timepoint: 0, collection_date: date("2025-01-15")}]->(s);

// Link sample to taxa (composition)
MATCH (s:Sample {id: "SMP_DEMO_001"})
MATCH (t1:Taxon {ncbi_id: "239935"})
MATCH (t2:Taxon {ncbi_id: "1680"})
MERGE (s)-[:CONTAINS {abundance: 0.05, reads: 2500, rank: 1}]->(t1)
MERGE (s)-[:CONTAINS {abundance: 0.08, reads: 4000, rank: 2}]->(t2);

// Create sample paper
MERGE (paper:Paper {
    pmid: "22797518",
    doi: "10.1073/pnas.1219451110",
    title: "Cross-talk between Akkermansia muciniphila and intestinal epithelium controls diet-induced obesity",
    abstract: "Obesity and type 2 diabetes are characterized by altered gut microbiota...",
    journal: "PNAS",
    publication_year: 2013,
    citation_count: 1234,
    keywords: ["obesity", "microbiome", "Akkermansia"],
    open_access: true,
    created_at: datetime()
});

// Link taxon to paper
MATCH (t:Taxon {ncbi_id: "239935"})
MATCH (p:Paper {pmid: "22797518"})
MERGE (t)-[:MENTIONED_IN {
    relevance_score: 0.95,
    context: "obesity",
    mention_count: 15,
    first_mentioned: 2013
}]->(p);

// ==============================================================================
// 6. VERIFICATION QUERIES
// ==============================================================================

// Count nodes by type
// MATCH (n) RETURN labels(n), COUNT(*) ORDER BY COUNT(*) DESC;

// Show sample data with relationships
// MATCH (p:Patient)-[:HAS_SAMPLE]->(s:Sample)-[:CONTAINS]->(t:Taxon)
// RETURN p.id, s.id, t.name, s.shannon_diversity
// LIMIT 10;

// Find papers mentioning a taxon
// MATCH (t:Taxon {name: "Akkermansia muciniphila"})-[r:MENTIONED_IN]->(p:Paper)
// RETURN p.title, p.publication_year, r.relevance_score
// ORDER BY r.relevance_score DESC
// LIMIT 5;

// Get taxonomic composition of a sample
// MATCH (s:Sample {id: "SMP_DEMO_001"})-[r:CONTAINS]->(t:Taxon)
// RETURN t.name, r.abundance, r.reads
// ORDER BY r.abundance DESC;

// ==============================================================================
// END OF SCHEMA
// ==============================================================================
