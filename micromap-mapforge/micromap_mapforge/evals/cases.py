"""#266: the curated mapping-quality eval cases.

Each case reuses one of the shipped ``examples/<discipline>/`` gold bundles — a
source CSV plus a hand-authored gold ``mapping.yaml`` (the "gold path" the issue
points to as production-grade). No new fixtures: the examples ARE the ground truth.

The set deliberately spans the two regimes #266 asks about:
- **conventional identifier columns** (disbiome, metabolomics, proteomics) — where
  the offline heuristic should already recover entities well;
- **relationship-heavy** (every case has a gold with 2-3 relationships) — which the
  heuristic never produces, so the LLM arm is where any relationship recall comes from.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: Repo root — the eval package sits at micromap-mapforge/micromap_mapforge/evals/,
#: the gold bundles at <repo>/examples/, matching tests/examples/*'s REPO_ROOT.
REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = REPO_ROOT / "examples"


@dataclass(frozen=True)
class EvalCase:
    name: str
    discipline: str          # schema_config template name (drives the heuristic)
    source_csv: Path         # the input the mapper profiles
    gold_mapping: Path       # hand-curated gold mapping.yaml
    columns_regime: str      # "conventional" | "mixed" — the entity-axis difficulty
    hint: str | None = None  # natural-language hint for the LLM arm


def default_cases() -> list[EvalCase]:
    def case(name, discipline, folder, csv, regime, hint=None) -> EvalCase:
        base = EXAMPLES / folder
        return EvalCase(name, discipline, base / csv, base / "mapping.yaml", regime, hint)

    return [
        case("microbiome/disbiome", "microbiome", "disbiome",
             "disbiome_sample.csv", "conventional",
             "Microbe-disease associations from literature, with PMID/DOI."),
        case("metabolomics/serum", "metabolomics", "metabolomics-serum-mini",
             "serum_metabolites.csv", "conventional",
             "HMDB serum metabolites with disease and KEGG pathway annotations."),
        case("proteomics/plasma", "proteomics", "proteomics-plasma-mini",
             "plasma_proteins.csv", "conventional",
             "Plasma proteins (UniProt) with disease associations."),
        case("transcriptomics/alzheimer", "transcriptomics", "transcriptomics-alzheimer-mini",
             "alzheimer_degs.csv", "mixed",
             "Differentially expressed genes in Alzheimer's, with fold change and p-value."),
        case("genomics/brca", "genomics", "genomics-brca-mini",
             "brca_variants.csv", "mixed",
             "BRCA1/2 variants with ClinVar IDs and disease/phenotype links."),
    ]
