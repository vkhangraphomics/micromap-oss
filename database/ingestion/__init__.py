"""
Graphomics Data Ingestion Pipelines

This package provides ETL pipelines for populating the Graphomics Knowledge Graph
from various external biomedical databases.

Supported data sources:
- NCBI Taxonomy: Microbial taxonomy hierarchy
- DrugBank: Drug-target interactions and drug metabolism (requires academic license)
- HMDB: Human metabolome including microbial metabolites
- Disbiome: Disease-microbiome associations
- KEGG: Metabolic pathways
- ChEMBL: Drug-target interactions (open-access, CC BY-SA 3.0)
- PubChem: Compound data and bioactivity (public domain)
- CARD: Antibiotic resistance data
- gutMDisorder: Gut microbiota-disorder associations
- GMrepo: Human gut metagenome repository
- BugSigDB: Microbiome differential abundance signatures
- PubMed: Biomedical literature metadata
- SemMedDB: Literature-derived biomedical relationships
- mBodyMap: Body site-specific microbiome reference data
- Reactome: Human pathway data (metabolism, immune, signaling)
- HMDBMetabolomics: HMDB human endogenous metabolites (metabolomics discipline)
- MetaCyc: MetaCyc compounds and pathways (metabolomics discipline)
- MetaboLights: MetaboLights metabolomics study data (metabolomics discipline)
- UniProtProteomics: UniProtKB proteins + PTMs + disease/pathway edges (proteomics discipline)
- PhosphoSite: PhosphoSitePlus modification sites (proteomics discipline)
- PRIDE: PRIDE archive proteomics studies + measurements (proteomics discipline)
"""

from .ncbi_taxonomy_loader import NCBITaxonomyLoader
from .drugbank_loader import DrugBankLoader
from .hmdb_loader import HMDBLoader
from .disbiome_loader import DisbiomeLoader
from .kegg_loader import KEGGLoader
from .chembl_loader import ChEMBLLoader
from .pubchem_loader import PubChemLoader
from .card_loader import CARDLoader
from .gutmdisorder_loader import GutMDisorderLoader
from .gmrepo_loader import GMrepoLoader
from .bugsigdb_loader import BugSigDBLoader
from .pubmed_loader import PubMedLoader
from .semmeddb_loader import SemMedDBLoader
from .mbodymap_loader import MBodyMapLoader
from .reactome_loader import ReactomeLoader
from .hmdb_metabolomics_loader import HMDBMetabolomicsLoader
from .metacyc_loader import MetaCycLoader
from .metabo_lights_loader import MetaboLightsLoader
from .uniprot_proteomics_loader import UniProtProteomicsLoader
from .phosphosite_loader import PhosphoSiteLoader
from .pride_loader import PRIDELoader

__all__ = [
    "NCBITaxonomyLoader",
    "DrugBankLoader",
    "HMDBLoader",
    "DisbiomeLoader",
    "KEGGLoader",
    "ChEMBLLoader",
    "PubChemLoader",
    "CARDLoader",
    "GutMDisorderLoader",
    "GMrepoLoader",
    "BugSigDBLoader",
    "PubMedLoader",
    "SemMedDBLoader",
    "MBodyMapLoader",
    "ReactomeLoader",
    "HMDBMetabolomicsLoader",
    "MetaCycLoader",
    "MetaboLightsLoader",
    "UniProtProteomicsLoader",
    "PhosphoSiteLoader",
    "PRIDELoader",
]
