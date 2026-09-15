"""Normalization helpers for disease names and canonical IDs.

Ported from graphomics-kg/database/ingestion/base_loader.py to keep
MapForge standalone. Keep in sync manually if the KG loader evolves.
"""

import re
from typing import Optional


DISEASE_ABBREVIATIONS = {
    "mdd": "major depressive disorder",
    "ibd": "inflammatory bowel disease",
    "ibs": "irritable bowel syndrome",
    "t2d": "type 2 diabetes",
    "t2dm": "type 2 diabetes",
    "crc": "colorectal cancer",
    "nafld": "non-alcoholic fatty liver disease",
    "ad": "alzheimer's disease",
    "pd": "parkinson's disease",
    "ms": "multiple sclerosis",
    "ra": "rheumatoid arthritis",
    "asd": "autism spectrum disorder",
    "uc": "ulcerative colitis",
    "cd": "crohn's disease",
}


def normalize_disease_name(name: str) -> str:
    """Normalize disease name for consistent matching across data sources.

    Handles:
    - Case normalization
    - Abbreviation expansion (IBD, MDD, T2D, …)
    - Punctuation removal (apostrophes, hyphens)
    - Whitespace normalization
    """
    if not name:
        return ""
    normalized = name.strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)

    if normalized in DISEASE_ABBREVIATIONS:
        normalized = DISEASE_ABBREVIATIONS[normalized]

    normalized = normalized.replace("'", "").replace("’", "")
    normalized = normalized.replace("-", " ")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def generate_disease_id(name: str, identifiers: Optional[dict] = None) -> str:
    """Generate a stable disease ID, preferring standard identifiers.

    Priority: DOID → MeSH → OMIM → UMLS → ICD-10 → name-based fallback.
    """
    if identifiers:
        if identifiers.get("doid"):
            return f"DOID:{identifiers['doid']}"
        if identifiers.get("mesh_id"):
            return f"MESH:{identifiers['mesh_id']}"
        if identifiers.get("omim_id"):
            return f"OMIM:{identifiers['omim_id']}"
        if identifiers.get("umls_cui"):
            return f"UMLS:{identifiers['umls_cui']}"
        if identifiers.get("icd10"):
            return f"ICD10:{identifiers['icd10']}"

    normalized = normalize_disease_name(name)
    return f"disease:{normalized.replace(' ', '_')}"
