"""
Curated Microbial Metabolite Production Loader

Creates (Taxon)-[:PRODUCES]->(Metabolite) relationships based on
well-established literature evidence.

Sources:
- HMDB microbial metabolite annotations
- MetaCyc bacterial metabolic pathways
- Published literature on gut microbiome metabolomics

Key metabolite categories:
1. Short-chain fatty acids (SCFAs): acetate, propionate, butyrate
2. Amino acid metabolites: tryptophan derivatives, GABA
3. Bile acid metabolites
4. Vitamins: B vitamins, K2
5. Neurotransmitters and precursors
"""

import os
import logging
from typing import Dict, List, Any, Iterator, Optional
from neo4j import Driver

from .base_loader import BaseLoader

logger = logging.getLogger(__name__)


# =============================================================================
# Explicit curation decisions for curated names that >1 HMDB entry claims (#276).
#
# Only needed where the resolution ladder in _resolve_canonical_compound cannot
# settle it on its own. Measured against the real hmdb_metabolites.xml
# (2026-07-15) across all 47 curated names and 217,920 HMDB entries:
#
#   33 names -> exactly one candidate          (resolve automatically)
#    3 names -> no candidate                    (amuc_1100 / "antimicrobial
#               peptides" aren't metabolites; isourolithin a isn't in HMDB)
#   11 names -> ambiguous, of which 8 are settled by a unique exact-NAME match
#               outranking the synonym hits (vitamin k2, formate, phenol,
#               riboflavin, lithocholic acid, indole, urobilinogen, equol)
#
# That leaves the three below: every candidate matches by synonym only, so the
# ladder has nothing to prefer and (correctly) refuses. These are curation
# calls, not something to infer — recorded here rather than guessed at runtime.
#
# Keys must be curated names (see TAXON_METABOLITE_PRODUCERS); values are HMDB
# accessions. A stale mapping fails loudly rather than silently picking wrong.
# HMDB evidence tiers that mean "this compound was actually observed", as
# opposed to `expected` / `predicted` (in-silico stubs) or a missing status.
# Used by _resolve_canonical_compound (#276).
_REAL_EVIDENCE = frozenset({"quantified", "detected"})


def _is_real_evidence(status: Optional[str]) -> bool:
    """True if HMDB actually observed this compound, rather than predicting it."""
    return bool(status) and status.strip().lower() in _REAL_EVIDENCE


CURATED_COMPOUND_HMDB_IDS = {
    # Acetic acid, NOT Acetylglycine (HMDB0000532) — HMDB lists a conjugate's
    # acyl moiety among its synonyms, so "Acetate" hits both. This one matters
    # most: acetate carries 43,108 producer edges, the largest bucket in the graph.
    "acetate": "HMDB0000042",

    # L-Lactic acid. D-Lactic acid (HMDB0001311) is also genuinely microbial and
    # is an equally valid reading of the curated "Lactate" claim; L is the
    # better-annotated canonical entry and the dominant physiological form.
    # Revisit if the curated data ever distinguishes the enantiomers.
    # (The third candidate, Indoleacetic acid HMDB0000197, lists "lactate" as a
    # synonym — plain HMDB noise.)
    "lactate": "HMDB0000190",

    # Cobalamin — the generic vitamin B12 microbes actually synthesise.
    # Cyanocobalamin (HMDB0000607) is the semi-synthetic supplement form.
    "vitamin b12": "HMDB0002174",
}

# CURATED TAXON-METABOLITE PRODUCTION DATA
# =============================================================================
# Format: taxon_pattern -> list of (metabolite_name, evidence_level, notes)
# taxon_pattern can be genus name (will match all species)
# evidence_level: 'high' (multiple studies), 'medium' (few studies), 'low' (theoretical)

TAXON_METABOLITE_PRODUCERS = {
    # ==========================================================================
    # SHORT-CHAIN FATTY ACIDS (SCFAs)
    # ==========================================================================

    # BUTYRATE PRODUCERS
    "Faecalibacterium": [
        ("butyrate", "high", "Major butyrate producer in human gut"),
        ("acetate", "medium", "Cross-feeding product"),
        ("formate", "medium", "Fermentation byproduct"),
    ],
    "Faecalibacterium prausnitzii": [
        ("butyrate", "high", "Primary butyrate producer, 5-10% of gut microbiota"),
    ],
    "Roseburia": [
        ("butyrate", "high", "Abundant butyrate producer"),
        ("acetate", "medium", "Fermentation intermediate"),
    ],
    "Roseburia intestinalis": [
        ("butyrate", "high", "Major intestinal butyrate producer"),
    ],
    "Roseburia hominis": [
        ("butyrate", "high", "Butyrate from acetate + lactate"),
    ],
    "Roseburia inulinivorans": [
        ("butyrate", "high", "Butyrate from inulin fermentation"),
        ("propionate", "medium", "Also produces propionate"),
    ],
    "Eubacterium": [
        ("butyrate", "high", "Significant butyrate producer"),
        ("acetate", "medium", "Acetate production from fiber"),
        # Merged from former duplicate-key occurrence in "Hydrogen Producers" section (#165).
        ("hydrogen", "medium", "H2 producer"),
    ],
    "Eubacterium rectale": [
        ("butyrate", "high", "One of top 5 butyrate producers"),
    ],
    "Eubacterium hallii": [
        ("butyrate", "high", "Converts lactate to butyrate"),
        ("propionate", "medium", "1,2-propanediol to propionate"),
    ],
    "Eubacterium limosum": [
        ("butyrate", "high", "Acetogenic butyrate producer"),
        ("acetate", "high", "CO2/H2 to acetate"),
    ],
    "Coprococcus": [
        ("butyrate", "high", "Butyrate producer via butyryl-CoA"),
        ("propionate", "low", "Minor propionate production"),
    ],
    "Coprococcus catus": [
        ("butyrate", "high", "Butyrate from lactate"),
        ("propionate", "medium", "Via acrylate pathway"),
    ],
    "Coprococcus eutactus": [
        ("butyrate", "high", "Fiber-fermenting butyrate producer"),
    ],
    "Coprococcus comes": [
        ("butyrate", "high", "Linked to mental health via butyrate"),
    ],
    "Anaerostipes": [
        ("butyrate", "high", "Acetate-to-butyrate converter"),
        ("lactate", "medium", "Intermediate metabolism"),
    ],
    "Anaerostipes caccae": [
        ("butyrate", "high", "Cross-feeds with Bifidobacterium"),
    ],
    "Anaerostipes hadrus": [
        ("butyrate", "high", "Lactate-utilizing butyrate producer"),
    ],
    "Butyricicoccus": [
        ("butyrate", "high", "Named for butyrate production"),
    ],
    "Butyricicoccus pullicaecorum": [
        ("butyrate", "high", "Anti-inflammatory butyrate producer"),
    ],
    "Subdoligranulum": [
        ("butyrate", "medium", "Butyrate producer in healthy gut"),
    ],
    "Subdoligranulum variabile": [
        ("butyrate", "high", "Abundant in healthy microbiome"),
    ],
    "Clostridium": [
        ("butyrate", "medium", "Many species produce butyrate"),
        ("acetate", "medium", "Acetogenic metabolism"),
        ("propionate", "low", "Some species produce propionate"),
        # Merged from former duplicate-key occurrences (#165): tryptamine
        # section, TMA/TMAO section, and hydrogen-producers section.
        ("tryptamine", "low", "Some species"),
        ("trimethylamine", "medium", "TMA from choline/carnitine"),
        ("hydrogen", "medium", "Fermentation product"),
    ],
    "Clostridium butyricum": [
        ("butyrate", "high", "Probiotic butyrate producer"),
    ],
    "Clostridium beijerinckii": [
        ("butyrate", "high", "Industrial butyrate producer"),
        ("acetone", "medium", "ABE fermentation"),
        ("butanol", "medium", "ABE fermentation"),
    ],
    "Clostridium tyrobutyricum": [
        ("butyrate", "high", "High-yield butyrate producer"),
    ],
    "Ruminococcus": [
        ("butyrate", "medium", "Some species produce butyrate"),
        ("acetate", "high", "Major acetate producer"),
        ("formate", "medium", "Hydrogen/formate producer"),
        # Merged from former duplicate-key occurrence in "Hydrogen Producers" section (#165).
        ("hydrogen", "high", "H2 for cross-feeding"),
    ],
    "Ruminococcus bromii": [
        ("acetate", "high", "Resistant starch degrader, keystone species"),
    ],
    "Ruminococcus gnavus": [
        ("acetate", "high", "Mucin degrader"),
        ("formate", "medium", "Formate producer"),
        ("tryptamine", "medium", "Tryptophan decarboxylase"),
    ],
    "Oscillibacter": [
        ("butyrate", "medium", "Health-associated butyrate producer"),
        ("valerate", "medium", "Produces valerate from proteins"),
    ],
    "Oscillospira": [
        ("butyrate", "medium", "Associated with leanness"),
    ],
    "Butyrivibrio": [
        ("butyrate", "high", "Named for butyrate production"),
    ],
    "Butyrivibrio fibrisolvens": [
        ("butyrate", "high", "Fiber-degrading butyrate producer"),
    ],
    "Lachnospira": [
        ("butyrate", "medium", "Pectin-fermenting butyrate producer"),
        ("acetate", "medium", "Co-produces acetate"),
    ],
    "Lachnospira pectinoschiza": [
        ("butyrate", "medium", "Pectin specialist"),
    ],

    # PROPIONATE PRODUCERS
    "Bacteroides": [
        ("propionate", "high", "Major propionate producer via succinate"),
        ("acetate", "high", "Abundant acetate production"),
        ("succinate", "medium", "Intermediate metabolite"),
        # Merged from former duplicate-key occurrence in "Polyamine Producers" section (#165).
        ("putrescine", "medium", "Polyamine"),
    ],
    "Bacteroides fragilis": [
        ("propionate", "high", "Polysaccharide A immunomodulator"),
        ("acetate", "high", "Major fermentation product"),
    ],
    "Bacteroides thetaiotaomicron": [
        ("propionate", "high", "Versatile glycan degrader"),
        ("acetate", "high", "Primary metabolite"),
    ],
    "Bacteroides vulgatus": [
        ("propionate", "high", "Common gut Bacteroides"),
        ("succinate", "medium", "Succinate intermediate"),
    ],
    "Bacteroides uniformis": [
        ("propionate", "high", "Fiber fermentation"),
    ],
    "Bacteroides ovatus": [
        ("propionate", "high", "Inulin degrader"),
    ],
    "Prevotella": [
        ("propionate", "high", "Propionate from carbohydrates"),
        ("acetate", "medium", "Acetate co-production"),
        ("succinate", "medium", "Succinate pathway"),
    ],
    "Prevotella copri": [
        ("propionate", "high", "Plant fiber specialist"),
        ("succinate", "high", "Key intermediate"),
    ],
    "Veillonella": [
        ("propionate", "high", "Lactate-to-propionate converter"),
        ("acetate", "medium", "Acetate co-production"),
    ],
    "Veillonella parvula": [
        ("propionate", "high", "Oral and gut lactate utilizer"),
    ],
    "Dialister": [
        ("propionate", "medium", "Propionate producer"),
    ],
    "Dialister invisus": [
        ("propionate", "medium", "Succinate to propionate"),
    ],
    "Phascolarctobacterium": [
        ("propionate", "high", "Succinate-to-propionate pathway"),
        ("acetate", "medium", "Co-product"),
    ],
    "Phascolarctobacterium faecium": [
        ("propionate", "high", "Dominant propionate producer"),
    ],
    "Megasphaera": [
        ("propionate", "medium", "Propionate from lactate"),
        ("butyrate", "medium", "Also produces butyrate"),
        ("valerate", "medium", "Branched-chain fatty acid"),
        ("caproate", "low", "Chain elongation"),
    ],
    "Megasphaera elsdenii": [
        ("propionate", "high", "Lactate utilizer in rumen/gut"),
        ("butyrate", "high", "Multi-SCFA producer"),
        ("valerate", "medium", "From lactate"),
    ],

    # ACETATE PRODUCERS (most abundant SCFA)
    "Blautia": [
        ("acetate", "high", "Acetogenic bacterium"),
        ("butyrate", "low", "Minor butyrate production"),
    ],
    "Blautia hydrogenotrophica": [
        ("acetate", "high", "H2/CO2 to acetate (acetogen)"),
    ],
    "Blautia obeum": [
        ("acetate", "high", "Common gut acetogen"),
    ],
    "Blautia wexlerae": [
        ("acetate", "high", "GABA metabolism"),
    ],
    "Dorea": [
        ("acetate", "high", "Significant acetate producer"),
        ("formate", "medium", "Co-product"),
    ],
    "Dorea formicigenerans": [
        ("acetate", "high", "Formate co-producer"),
        ("formate", "high", "Named for formate production"),
    ],
    "Dorea longicatena": [
        ("acetate", "high", "Mucin degrader"),
    ],
    "Streptococcus": [
        ("lactate", "high", "Lactic acid producer"),
        ("acetate", "medium", "Mixed acid fermentation"),
    ],
    "Streptococcus thermophilus": [
        ("lactate", "high", "Yogurt starter culture"),
        ("folate", "medium", "Folate producer"),
    ],
    "Streptococcus salivarius": [
        ("lactate", "high", "Oral commensal"),
    ],
    "Acetobacterium": [
        ("acetate", "high", "Obligate acetogen"),
    ],
    "Marvinbryantia": [
        ("acetate", "high", "Acetogenic Lachnospiraceae"),
    ],

    # ==========================================================================
    # AMINO ACID METABOLITES
    # ==========================================================================

    # TRYPTOPHAN METABOLITES
    "Lactobacillus": [
        ("indole-3-lactic acid", "high", "Tryptophan metabolite"),
        ("indole-3-aldehyde", "medium", "AhR ligand"),
        ("lactate", "high", "Primary metabolite"),
        ("acetate", "medium", "Heterofermentative strains"),
        ("GABA", "medium", "Some strains produce GABA"),
    ],
    "Lactobacillus reuteri": [
        ("indole-3-aldehyde", "high", "AhR activation, immune modulation"),
        ("histamine", "high", "Anti-inflammatory histamine"),
        ("lactate", "high", "Primary metabolite"),
    ],
    "Lactobacillus rhamnosus": [
        ("lactate", "high", "Probiotic strain"),
        ("GABA", "medium", "Anxiety/depression effects"),
    ],
    "Lactobacillus acidophilus": [
        ("lactate", "high", "Acidophilin producer"),
        ("hydrogen peroxide", "medium", "Antimicrobial"),
    ],
    "Lactobacillus johnsonii": [
        ("lactate", "high", "Gut colonizer"),
        ("hydrogen peroxide", "medium", "Pathogen inhibition"),
    ],
    "Lactobacillus gasseri": [
        ("lactate", "high", "Weight management associations"),
    ],
    "Lactobacillus casei": [
        ("lactate", "high", "Cheese starter"),
        ("GABA", "low", "Some strains"),
    ],
    "Bifidobacterium": [
        ("acetate", "high", "Major fermentation product"),
        ("lactate", "high", "Lactic acid production"),
        ("folate", "medium", "Vitamin B9 synthesis"),
        ("GABA", "low", "Some species produce GABA"),
    ],
    "Bifidobacterium longum": [
        ("acetate", "high", "Infant gut colonizer"),
        ("lactate", "high", "Bifidus pathway"),
        ("folate", "high", "B9 producer"),
        ("GABA", "medium", "Psychobiotic effects"),
    ],
    "Bifidobacterium adolescentis": [
        ("acetate", "high", "Adult gut Bifidobacterium"),
        ("folate", "medium", "B vitamin synthesis"),
    ],
    "Bifidobacterium breve": [
        ("acetate", "high", "Infant colonizer"),
        ("conjugated linoleic acid", "medium", "CLA production"),
    ],
    "Bifidobacterium bifidum": [
        ("acetate", "high", "Mucin utilizer"),
        ("lactate", "high", "Bifidus pathway"),
    ],
    "Bifidobacterium dentium": [
        ("GABA", "high", "High GABA producer"),
        ("acetate", "high", "Primary metabolite"),
    ],
    "Peptostreptococcus": [
        ("indole", "high", "Tryptophan degradation"),
        ("phenol", "medium", "Tyrosine degradation"),
        ("p-cresol", "medium", "Uremic toxin precursor"),
    ],
    "Peptostreptococcus anaerobius": [
        ("indole", "high", "Amino acid fermenter"),
    ],
    "Clostridium sporogenes": [
        ("indole-3-propionic acid", "high", "Neuroprotective metabolite"),
        ("indole", "medium", "Tryptophan degradation"),
        ("tryptamine", "medium", "Decarboxylation product"),
        ("phenylacetic acid", "medium", "Phenylalanine metabolite"),
    ],
    "Peptoniphilus": [
        ("indole", "medium", "Tryptophan degrader"),
        ("phenol", "medium", "Aromatic amino acid metabolism"),
    ],

    # GABA AND NEUROTRANSMITTER PRECURSORS
    "Lactobacillus brevis": [
        ("GABA", "high", "Well-known GABA producer"),
        ("lactate", "high", "Heterofermentative"),
    ],
    "Lactobacillus plantarum": [
        ("GABA", "medium", "GABA production capability"),
        ("histamine", "low", "Some strains"),
        ("lactate", "high", "Primary metabolite"),
    ],
    "Lactobacillus paracasei": [
        ("GABA", "medium", "GABA producer"),
        ("lactate", "high", "Primary metabolite"),
    ],

    # TYRAMINE AND PHENYLETHYLAMINE
    "Enterococcus faecalis": [
        ("tyramine", "high", "Tyrosine decarboxylase"),
        ("phenylethylamine", "medium", "Biogenic amine"),
        ("lactate", "high", "Fermentation product"),
    ],
    "Enterococcus faecium": [
        ("tyramine", "medium", "Biogenic amine producer"),
        ("lactate", "high", "Primary metabolite"),
    ],

    # TRYPTAMINE PRODUCERS
    # (Clostridium tryptamine claim merged into the earlier SCFA-section
    # entry above — see #165.)

    # ==========================================================================
    # BILE ACID METABOLITES
    # ==========================================================================
    "Clostridium scindens": [
        ("deoxycholic acid", "high", "7α-dehydroxylation of primary bile acids"),
        ("lithocholic acid", "high", "Secondary bile acid"),
    ],
    "Clostridium hiranonis": [
        ("deoxycholic acid", "high", "Bile acid converter"),
        ("lithocholic acid", "high", "From CDCA"),
    ],
    "Clostridium hylemonae": [
        ("deoxycholic acid", "high", "7α-dehydroxylase"),
        ("lithocholic acid", "high", "Secondary bile acid"),
    ],
    "Eggerthella": [
        ("urobilinogen", "medium", "Bilirubin metabolism"),
    ],
    "Eggerthella lenta": [
        ("urobilinogen", "medium", "Bilirubin to urobilinogen"),
        ("equol", "low", "Isoflavone metabolism"),
    ],
    # (Bacteroides fragilis, Lactobacillus, and Bifidobacterium entries here
    # were exact duplicates of their earlier SCFA/tryptophan-section entries
    # and dropped in #165 — Python kept only the last occurrence anyway.)

    # ==========================================================================
    # VITAMINS
    # ==========================================================================
    "Propionibacterium": [
        ("vitamin B12", "high", "Cobalamin synthesis"),
        ("propionate", "high", "Propionate producer"),
    ],
    "Propionibacterium freudenreichii": [
        ("vitamin B12", "high", "Industrial B12 producer"),
        ("propionate", "high", "Swiss cheese holes"),
        ("folate", "medium", "B vitamin synthesis"),
    ],
    "Bacillus": [
        ("vitamin K2", "medium", "Menaquinone synthesis"),
        ("riboflavin", "low", "B2 synthesis"),
    ],
    "Bacillus subtilis": [
        ("vitamin K2", "high", "Natto production, MK-7"),
        ("riboflavin", "medium", "B2 producer"),
        ("biotin", "low", "B7 synthesis"),
    ],
    "Lactococcus lactis": [
        ("riboflavin", "high", "B2 overproducer strains"),
        ("folate", "high", "B9 synthesis"),
        ("lactate", "high", "Primary metabolite"),
        ("vitamin K2", "medium", "MK-8, MK-9"),
    ],
    "Leuconostoc": [
        ("vitamin B12", "low", "Some strains"),
        ("folate", "medium", "B9 synthesis"),
        ("lactate", "high", "Heterofermentative"),
    ],
    # (Streptococcus thermophilus entry here was an exact duplicate of the
    # earlier yogurt-starter section entry and dropped in #165.)

    # ==========================================================================
    # AKKERMANSIA - Mucin degrader
    # ==========================================================================
    "Akkermansia": [
        ("acetate", "high", "Major metabolite from mucin"),
        ("propionate", "high", "From mucin degradation"),
        ("butyrate", "low", "Minimal butyrate production"),
    ],
    "Akkermansia muciniphila": [
        ("acetate", "high", "Cross-feeding substrate"),
        ("propionate", "high", "Mucin-derived"),
        ("amuc_1100", "high", "Outer membrane protein"),
    ],

    # ==========================================================================
    # ENTEROBACTERIACEAE - Mixed acid fermenters
    # ==========================================================================
    "Escherichia": [
        ("acetate", "high", "Mixed acid fermentation"),
        ("lactate", "medium", "Fermentation product"),
        ("ethanol", "medium", "Anaerobic conditions"),
        ("succinate", "medium", "TCA intermediate"),
        ("indole", "medium", "Tryptophanase activity"),
    ],
    "Escherichia coli": [
        ("indole", "high", "Major indole producer"),
        ("acetate", "high", "Mixed acid fermentation"),
        ("vitamin K2", "medium", "MK-8 producer"),
        ("succinate", "medium", "TCA cycle"),
        ("formate", "medium", "Formate-hydrogen lyase"),
    ],
    "Klebsiella": [
        ("2,3-butanediol", "high", "Butanediol fermentation"),
        ("acetate", "medium", "Mixed acid fermentation"),
    ],
    "Klebsiella pneumoniae": [
        ("2,3-butanediol", "high", "Characteristic metabolite"),
        ("ethanol", "medium", "Fermentation product"),
    ],
    "Proteus": [
        ("indole", "high", "Tryptophanase positive"),
        ("ammonia", "high", "Urease activity"),
        ("hydrogen sulfide", "medium", "Cysteine desulfhydrase"),
    ],
    "Proteus mirabilis": [
        ("ammonia", "high", "Strong urease producer"),
        ("indole", "high", "Tryptophan metabolism"),
    ],
    "Citrobacter": [
        ("indole", "medium", "Variable indole production"),
        ("hydrogen sulfide", "medium", "H2S positive"),
    ],
    "Enterobacter": [
        ("2,3-butanediol", "medium", "Butanediol pathway"),
        ("acetate", "medium", "Mixed acid fermentation"),
    ],
    "Serratia": [
        ("prodigiosin", "high", "Red pigment"),
        ("acetate", "medium", "Fermentation product"),
    ],

    # ==========================================================================
    # SULFATE REDUCERS AND H2S PRODUCERS
    # ==========================================================================
    "Desulfovibrio": [
        ("hydrogen sulfide", "high", "Sulfate reduction"),
        ("acetate", "medium", "Incomplete oxidation"),
    ],
    "Desulfovibrio piger": [
        ("hydrogen sulfide", "high", "Gut sulfate reducer"),
    ],
    "Bilophila": [
        ("hydrogen sulfide", "high", "Taurine degradation"),
    ],
    "Bilophila wadsworthia": [
        ("hydrogen sulfide", "high", "Taurine/sulfite to H2S"),
    ],

    # ==========================================================================
    # METHANOGENS (Archaea)
    # ==========================================================================
    "Methanobrevibacter": [
        ("methane", "high", "Methanogenesis"),
    ],
    "Methanobrevibacter smithii": [
        ("methane", "high", "Primary human gut methanogen"),
    ],
    "Methanosphaera": [
        ("methane", "high", "Methanol-utilizing methanogen"),
    ],
    "Methanosphaera stadtmanae": [
        ("methane", "high", "H2 + methanol to methane"),
    ],

    # ==========================================================================
    # PROTEOLYTIC BACTERIA
    # ==========================================================================
    "Fusobacterium": [
        ("ammonia", "high", "Amino acid deamination"),
        ("butyrate", "medium", "From amino acid fermentation"),
        ("hydrogen sulfide", "medium", "Cysteine degradation"),
        # Merged from former duplicate-key occurrence in "Polyamine Producers" section (#165).
        ("putrescine", "medium", "From arginine/ornithine"),
    ],
    "Fusobacterium nucleatum": [
        ("butyrate", "high", "Amino acid-derived butyrate"),
        ("ammonia", "high", "Glutamate metabolism"),
        ("hydrogen sulfide", "medium", "Cysteine metabolism"),
    ],
    "Porphyromonas": [
        ("ammonia", "high", "Proteolytic metabolism"),
        ("hydrogen sulfide", "medium", "Sulfur amino acid metabolism"),
    ],
    "Porphyromonas gingivalis": [
        ("hydrogen sulfide", "high", "Periodontal pathogen"),
        ("ammonia", "high", "Protein degradation"),
        ("indole", "medium", "Tryptophan catabolism"),
    ],

    # ==========================================================================
    # TMA/TMAO PRODUCERS (Cardiovascular relevance)
    # ==========================================================================
    # (Clostridium trimethylamine claim merged into the earlier SCFA-section
    # entry above — see #165.)
    "Clostridium asparagiforme": [
        ("trimethylamine", "high", "Choline to TMA"),
    ],
    "Clostridium hathewayi": [
        ("trimethylamine", "high", "TMA producer from choline"),
    ],
    "Providencia": [
        ("trimethylamine", "high", "Carnitine to TMA"),
    ],
    "Providencia rettgeri": [
        ("trimethylamine", "high", "Strong TMA producer"),
    ],
    "Edwardsiella": [
        ("trimethylamine", "medium", "TMA production"),
    ],
    "Edwardsiella tarda": [
        ("trimethylamine", "high", "Fish spoilage, TMA"),
    ],
    "Anaerococcus": [
        ("trimethylamine", "medium", "TMA from choline"),
    ],
    "Anaerococcus hydrogenalis": [
        ("trimethylamine", "high", "Choline TMA-lyase"),
    ],

    # ==========================================================================
    # UREMIC TOXIN PRODUCERS
    # ==========================================================================
    "Clostridioides": [
        ("p-cresol", "medium", "Uremic toxin precursor"),
    ],
    "Clostridioides difficile": [
        ("p-cresol", "high", "Tyrosine to p-cresol"),
        ("butyrate", "low", "Some production"),
    ],
    # (Peptostreptococcus entry here was an exact duplicate of the earlier
    # tryptophan-metabolites section entry and dropped in #165.)
    "Clostridium difficile": [
        ("p-cresol", "high", "Tyrosine decarboxylase"),
    ],

    # ==========================================================================
    # POLYPHENOL METABOLIZERS
    # ==========================================================================
    "Gordonibacter": [
        ("urolithin A", "high", "Ellagic acid metabolism"),
        ("urolithin B", "medium", "Ellagitannin metabolite"),
    ],
    "Gordonibacter urolithinfaciens": [
        ("urolithin A", "high", "Key urolithin producer"),
    ],
    "Gordonibacter pamelaeae": [
        ("urolithin A", "medium", "Urolithin production"),
    ],
    "Ellagibacter": [
        ("urolithin A", "high", "Ellagitannin metabolizer"),
    ],
    "Ellagibacter isourolithinifaciens": [
        ("isourolithin A", "high", "Isourolithin producer"),
    ],
    "Slackia": [
        ("equol", "high", "Isoflavone metabolism"),
    ],
    "Slackia isoflavoniconvertens": [
        ("equol", "high", "Daidzein to equol"),
    ],
    "Slackia equolifaciens": [
        ("equol", "high", "Named for equol production"),
    ],
    "Adlercreutzia": [
        ("equol", "high", "Isoflavone metabolizer"),
    ],
    "Adlercreutzia equolifaciens": [
        ("equol", "high", "Major equol producer"),
    ],

    # ==========================================================================
    # ADDITIONAL COMMON GUT BACTERIA
    # ==========================================================================
    "Alistipes": [
        ("acetate", "medium", "Fermentation product"),
        ("succinate", "medium", "Carbohydrate fermentation"),
        ("indole", "low", "Some tryptophan metabolism"),
    ],
    "Alistipes putredinis": [
        ("acetate", "high", "Primary metabolite"),
        ("succinate", "medium", "Intermediate"),
    ],
    "Alistipes finegoldii": [
        ("acetate", "high", "SCFA producer"),
    ],
    "Parabacteroides": [
        ("acetate", "medium", "Fermentation product"),
        ("propionate", "medium", "From succinate pathway"),
        ("succinate", "medium", "Intermediate"),
    ],
    "Parabacteroides distasonis": [
        ("propionate", "high", "Succinate pathway"),
        ("acetate", "high", "Primary metabolite"),
        ("lithocholic acid", "medium", "Bile acid metabolism"),
    ],
    "Parabacteroides merdae": [
        ("propionate", "high", "Propionate producer"),
    ],
    "Enterococcus": [
        ("lactate", "high", "Lactic acid bacteria"),
        ("acetate", "medium", "Mixed fermentation"),
    ],
    "Collinsella": [
        ("lactate", "medium", "Fermentation product"),
    ],
    "Collinsella aerofaciens": [
        ("lactate", "high", "Hydrogen/lactate producer"),
        ("formate", "medium", "Co-product"),
    ],
    "Agathobaculum": [
        ("butyrate", "medium", "Butyrate producer (formerly Eubacterium)"),
    ],
    "Agathobaculum butyriciproducens": [
        ("butyrate", "high", "Named for butyrate production"),
    ],
    "Christensenella": [
        ("acetate", "medium", "Associated with leanness"),
    ],
    "Christensenella minuta": [
        ("acetate", "high", "Heritable lean microbe"),
    ],
    "Odoribacter": [
        ("butyrate", "medium", "Butyrate producer"),
        ("acetate", "medium", "Fermentation product"),
    ],
    "Odoribacter splanchnicus": [
        ("butyrate", "high", "Fiber fermenter"),
    ],
    "Barnesiella": [
        ("acetate", "medium", "SCFA producer"),
        ("propionate", "medium", "Co-product"),
    ],
    "Barnesiella intestinihominis": [
        ("acetate", "high", "Healthy gut marker"),
    ],

    # ==========================================================================
    # ORAL MICROBIOME
    # ==========================================================================
    "Streptococcus mutans": [
        ("lactate", "high", "Dental caries - acid production"),
    ],
    "Streptococcus gordonii": [
        ("lactate", "high", "Early colonizer"),
        ("hydrogen peroxide", "medium", "Antimicrobial"),
    ],
    "Actinomyces": [
        ("lactate", "medium", "Dental plaque"),
        ("acetate", "medium", "Fermentation"),
    ],
    "Actinomyces naeslundii": [
        ("lactate", "high", "Plaque acidification"),
    ],
    # (Veillonella parvula entry here was an exact duplicate of the earlier
    # propionate-producers section entry and dropped in #165.)
    "Rothia": [
        ("acetate", "medium", "Oral commensal"),
    ],
    "Rothia mucilaginosa": [
        ("acetate", "medium", "Oral/respiratory"),
    ],

    # ==========================================================================
    # SOIL/ENVIRONMENTAL (probiotic spore formers)
    # ==========================================================================
    "Bacillus coagulans": [
        ("lactate", "high", "Spore-forming probiotic"),
    ],
    "Bacillus clausii": [
        ("riboflavin", "medium", "B2 producer"),
        ("antimicrobial peptides", "medium", "Bacteriocin-like"),
    ],

    # ==========================================================================
    # HYDROGEN PRODUCERS (important for cross-feeding)
    # ==========================================================================
    # (Ruminococcus, Clostridium, and Eubacterium hydrogen-production claims
    # merged into their earlier SCFA-section entries above — see #165.)

    # ==========================================================================
    # POLYAMINE PRODUCERS
    # ==========================================================================
    # (Bacteroides and Fusobacterium putrescine/polyamine claims merged into
    # their earlier propionate/proteolytic-section entries above — see #165.)
    "Campylobacter": [
        ("acetate", "medium", "Fermentation product"),
    ],
    "Campylobacter jejuni": [
        ("acetate", "medium", "Aspartate fermentation"),
    ],
}


class ProducesLoader(BaseLoader):
    """
    Loader for curated Taxon-Metabolite PRODUCES relationships.

    Creates (Taxon)-[:PRODUCES {evidence_level, notes, source}]->(Metabolite) relationships
    based on well-established literature evidence.
    """

    def __init__(
        self,
        driver: Driver,
        organization_id: str,
        batch_size: int = 500,
        database: str = "neo4j"
    ):
        """
        Initialize the loader.

        Args:
            driver: Neo4j driver instance
            organization_id: Organization ID for multi-tenant isolation
            batch_size: Number of records per batch
            database: Neo4j database name
        """
        super().__init__(driver, organization_id, batch_size, database)

    @property
    def source_name(self) -> str:
        return "curated_produces"

    def extract(self, **kwargs) -> Iterator[Dict[str, Any]]:
        """Extract taxon-metabolite relationships from curated data."""
        for taxon_pattern, metabolites in TAXON_METABOLITE_PRODUCERS.items():
            for metabolite_name, evidence_level, notes in metabolites:
                yield {
                    'taxon_pattern': taxon_pattern,
                    'metabolite_name': metabolite_name,
                    'evidence_level': evidence_level,
                    'notes': notes
                }

    def transform(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Transform record for loading."""
        return {
            'taxon_pattern': record['taxon_pattern'],
            'metabolite_name': record['metabolite_name'].lower(),
            'metabolite_display': record['metabolite_name'].title(),
            'evidence_level': record['evidence_level'],
            'notes': record['notes']
        }

    def _resolve_canonical_compound(
        self,
        name_lower: str,
        explicit_ids: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        """Resolve a curated metabolite name to ONE identifier-keyed compound_id.

        Returns None when nothing matches, or when the match is ambiguous — the
        caller then falls back to a curated-only node, i.e. the pre-#276
        behaviour. Refusing is deliberate: `acetate` carries 43,108 producer
        edges, so hanging them on the wrong molecule is far worse than leaving
        the (already known, already tracked) gap in place.

        Order of preference:
          0. an explicit curation decision (CURATED_COMPOUND_HMDB_IDS)
          1. best HMDB evidence tier available (quantified > detected >
             expected > predicted)
          2. within that: exact `name` > exact `iupac_name` > exact `synonyms`
        Uniqueness is required at whichever tier wins.

        Evidence tier comes FIRST, and that ordering is load-bearing: HMDB keeps
        predicted stubs that hold the plainer name. HMDB0304356 "formate" is
        `expected` with 0 diseases and 0 pathways, while HMDB0000142
        "Formic acid" is `quantified` with 14 diseases and 39 pathways and
        carries "Formate" only as a synonym. Preferring the name match would put
        formate's 4,376 producers on the empty stub — severed hop intact.

        The match tier then separates Acetic acid from Acetylglycine, since HMDB
        lists a conjugate's acyl moiety among its synonyms (#276/#277).
        """
        explicit_ids = CURATED_COMPOUND_HMDB_IDS if explicit_ids is None else explicit_ids

        candidates = self.execute_cypher(
            """
            MATCH (m:Compound)
            WHERE m.compound_id IS NOT NULL
              AND (toLower(m.name) = $name
                   OR toLower(coalesce(m.iupac_name, '')) = $name
                   OR any(s IN coalesce(m.synonyms, [])
                          WHERE toLower(s) = $name))
            RETURN m.compound_id  AS compound_id,
                   m.name         AS name,
                   m.hmdb_id      AS hmdb_id,
                   m.hmdb_status  AS hmdb_status,
                   toLower(m.name) = $name AS name_match,
                   toLower(coalesce(m.iupac_name, '')) = $name AS iupac_match
            """,
            {'name': name_lower},
            write=False,
        ) or []

        if not candidates:
            return None

        # 0. An explicit curation decision always wins.
        pinned = explicit_ids.get(name_lower)
        if pinned:
            for c in candidates:
                if c.get('hmdb_id') == pinned:
                    return c['compound_id']
            logger.warning(
                "Curated metabolite %r is pinned to %s in CURATED_COMPOUND_HMDB_IDS, "
                "but no loaded compound has that hmdb_id (candidates: %s). "
                "Falling back to a curated-only node — fix the mapping or load %s.",
                name_lower, pinned,
                [c.get('hmdb_id') for c in candidates], pinned,
            )
            return None

        # 1. Demote in-silico stubs — but ONLY that. If any candidate was really
        #    observed, drop the expected/predicted ones so a stub can't win on
        #    the strength of holding the plainer name (the "formate" case).
        #
        #    Deliberately NOT "keep only the single best tier": that let a
        #    better-evidenced SYNONYM outrank a real NAME match. On live data
        #    'vitamin k2' offers vitamin K2 [detected] (right, name match) and
        #    Menadione [quantified] (vitamin K3 — a different molecule, synonym
        #    match); best-tier-wins picked Menadione, and vitamin k2 carries
        #    37,832 producer edges. Evidence demotes stubs; match tier decides.
        if any(_is_real_evidence(c.get('hmdb_status')) for c in candidates):
            candidates = [
                c for c in candidates if _is_real_evidence(c.get('hmdb_status'))
            ]

        # 2. Best available match tier, uniqueness required.
        for tier in ('name_match', 'iupac_match', None):
            tier_hits = (
                [c for c in candidates if c.get(tier)] if tier
                else candidates  # synonym tier: whatever is left
            )
            if not tier_hits:
                continue
            if len(tier_hits) == 1:
                return tier_hits[0]['compound_id']
            logger.warning(
                "Curated metabolite %r is ambiguous: %d compounds claim it (%s). "
                "Refusing to guess — PRODUCES edges would land on the wrong "
                "molecule. Record the intended one in CURATED_COMPOUND_HMDB_IDS.",
                name_lower, len(tier_hits),
                [f"{c.get('name')} ({c.get('hmdb_id')})" for c in tier_hits],
            )
            return None

        return None

    def load_batch(self, batch: List[Dict[str, Any]]) -> Dict[str, int]:
        """Load a batch of PRODUCES relationships."""
        nodes_created = 0
        rels_created = 0

        for record in batch:
            taxon_pattern = record['taxon_pattern']
            metabolite_name = record['metabolite_name']
            metabolite_display = record['metabolite_display']
            evidence_level = record['evidence_level']
            notes = record['notes']

            # Attach curated PRODUCES claims to the same compounds HMDB / KEGG /
            # Reactome populate, rather than creating disjoint name_lower nodes
            # that hold producers and nothing else (umbrella #42, #271, #276).
            #
            # This probed `m.metabolite_id IS NOT NULL` until #276. Nothing has
            # written metabolite_id since the #146 label migration (commit
            # a2b8598 renamed the label in this query and left the predicate
            # behind), so the probe never matched, the else-branch always fired,
            # and ALL 218,454 PRODUCES edges landed on identifier-less nodes —
            # leaving zero taxon->compound->disease paths in the graph.
            #
            # Matching on name alone would not have saved it either: 'butyrate'
            # is not 'butyric acid'. Resolution now goes through synonyms
            # (loaded by #277), and refuses rather than guessing when ambiguous.
            canonical_id = self._resolve_canonical_compound(metabolite_name)

            if canonical_id:
                # Tag the ONE resolved node with name_lower so the PRODUCES
                # MERGE below finds it. Keyed on compound_id, never on the name
                # predicate: tagging every match would fan the edges out across
                # each ambiguous candidate.
                self.execute_cypher(
                    """
                    MATCH (m:Compound {compound_id: $compound_id})
                    SET m.name_lower = $name_lower,
                        m.sources = CASE
                            WHEN m.sources IS NULL THEN ['curated_produces']
                            WHEN 'curated_produces' IN m.sources THEN m.sources
                            ELSE m.sources + 'curated_produces'
                        END,
                        m.updated_at = datetime()
                    """,
                    {'compound_id': canonical_id, 'name_lower': metabolite_name}
                )
            else:
                # No HMDB/KEGG-loaded metabolite for this name yet; create a
                # curated-only node keyed by name_lower (legacy path).
                self.execute_cypher(
                    """
                    MERGE (m:Compound {name_lower: $name_lower})
                    ON CREATE SET
                        m.name = $name,
                        m.source = 'curated_produces',
                        m.sources = ['curated_produces'],
                        m.organization_id = $org_id,
                        m.created_at = datetime()
                    ON MATCH SET
                        m.updated_at = datetime()
                    RETURN m
                    """,
                    {
                        'name_lower': metabolite_name,
                        'name': metabolite_display,
                        'org_id': self.organization_id,
                    }
                )
                nodes_created += 1

            # Create PRODUCES relationships to matching Taxon nodes.
            # Resolution rules (rank-aware to avoid spurious sister-taxon
            # matches; see issue #48):
            #   1. Exact case-folded name match — applies to any rank.
            #   2. Genus-level claim ("Faecalibacterium") fans out to species
            #      under that genus via t.genus, not by name prefix.
            #   3. Species-level claim ("Faecalibacterium prausnitzii") fans
            #      out to its strains/subspecies via name-prefix match,
            #      restricted to ranks below species and requiring a space
            #      delimiter so the prefix is a complete name component
            #      (avoids "Faecalibacterium" matching "Faecalibacteriumlike").
            rel_query = """
                MATCH (m:Compound {name_lower: $metabolite})
                MATCH (t:Taxon)
                WHERE toLower(t.name) = toLower($taxon_pattern)
                   OR (t.genus = $taxon_pattern AND t.rank = 'species')
                   OR (t.name STARTS WITH $taxon_pattern + ' '
                       AND t.rank IN ['strain', 'subspecies', 'no rank'])
                MERGE (t)-[r:PRODUCES]->(m)
                ON CREATE SET
                    r.evidence_level = $evidence_level,
                    r.notes = $notes,
                    r.source = 'curated',
                    r.created_at = datetime()
                ON MATCH SET
                    r.updated_at = datetime()
                RETURN count(r) as count
            """
            result = self.execute_cypher(rel_query, {
                'metabolite': metabolite_name,
                'taxon_pattern': taxon_pattern,
                'evidence_level': evidence_level,
                'notes': notes
            })
            if result:
                rels_created += result[0].get('count', 0)

        return {
            'nodes_created': nodes_created,
            'relationships_created': rels_created
        }


def load_produces_data(
    driver: Driver,
    organization_id: str = "default"
) -> Dict[str, Any]:
    """
    Convenience function to load curated PRODUCES relationships.

    Args:
        driver: Neo4j driver instance
        organization_id: Organization ID

    Returns:
        Loading statistics
    """
    loader = ProducesLoader(
        driver=driver,
        organization_id=organization_id
    )

    stats = loader.run()
    return stats.to_dict()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    from neo4j import GraphDatabase

    # Connect to Neo4j
    driver = GraphDatabase.driver(
        os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        auth=(
            os.environ.get("NEO4J_USER", "neo4j"),
            os.environ.get("NEO4J_PASSWORD", "password")
        )
    )

    try:
        print("Loading curated PRODUCES relationships...")
        stats = load_produces_data(driver, organization_id="default")
        print("\nPRODUCES Loading Complete!")
        print(f"  Nodes created: {stats['nodes_created']}")
        print(f"  Relationships created: {stats['relationships_created']}")
        print(f"  Duration: {stats['duration_seconds']:.1f} seconds")
    finally:
        driver.close()
