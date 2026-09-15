# Citation and Licensing

This document covers how to cite MapForge, and the licensing status of every
piece of bundled reference data.

## Citing MapForge

If you use MapForge in your research or publish results that depend on it,
please cite it. The canonical metadata lives in
[`CITATION.cff`](../CITATION.cff) at the package root.

A `CITATION.cff` is the standard machine-readable citation format — GitHub
renders it as a "Cite this repository" button in the repo header, and tools
like Zenodo and the [cffconvert](https://github.com/citation-file-format/cffconvert)
CLI translate it into BibTeX, EndNote, RIS, etc.

To get a BibTeX entry:

```bash
pip install cffconvert
cffconvert --infile CITATION.cff --format bibtex
```

## Licensing — the MapForge package itself

MIT (selected under the
[MAC-56 license-selection ticket](https://graphomics.atlassian.net/browse/MAC-56),
2026-09-15, as part of standing up the public `micromap-oss` mirror for
GitHub #376). See `LICENSE` at the repo root.

## Bundled reference data

MapForge ships a small amount of curated reference data under `examples/`
for the worked walkthroughs. Each data source retains its own upstream
license; the MapForge package's license does not extend to the bundled data.

### `examples/disbiome/disbiome_sample.csv`

- **What it is:** 50 hand-curated microbiome-disease association rows
  matching the Disbiome export shape. Each row carries a `pmid` and `doi`
  pointing at the peer-reviewed publication that reported the association.
- **Source attribution:** the underlying associations are drawn from
  peer-reviewed primary literature; each row's `pmid` and `doi` are the
  primary references. The set is hand-curated for MapForge's walkthroughs
  rather than sliced from a particular Disbiome export, but it follows
  Disbiome's column shape so a real Disbiome dump (or a partner's CSV in
  the same shape) can use the same `mapping.yaml` without edits.
- **Disbiome upstream:** [`https://disbiome.ugent.be/`](https://disbiome.ugent.be/).
  Disbiome's own licensing terms apply when using a real Disbiome export;
  consult their site for current terms.
- **License within MapForge:** the curated sample is distributed under the
  same terms as the MapForge package (MIT).

### Test fixtures (`micromap-mapforge/tests/fixtures/`)

- **What:** small synthetic CSV/TSV/JSON/JSONL/SQL fixtures used by the unit
  tests (`study.csv`, `study.tsv`, etc.).
- **Source:** synthetic — no real-world data. Generated for testing only.
- **License:** the package's license.

## Identifiers used in the walkthroughs

MapForge walkthroughs and the Disbiome example reference identifiers from
several public ontologies and databases. None of these are bundled with
MapForge; they're referenced by ID:

| Source | Used for | License |
|---|---|---|
| NCBI Taxonomy | `ncbi_taxid` (Taxon match key) | Public domain. [Source.](https://www.ncbi.nlm.nih.gov/taxonomy) |
| Disease Ontology (DOID) | `doid` (Disease alt identifier) | CC0 1.0. [Source.](https://disease-ontology.org/) |
| PubMed | `pmid` (Paper match key) | NCBI public-domain metadata. [Source.](https://pubmed.ncbi.nlm.nih.gov/) |
| DOI / Crossref | `doi` (Paper alt identifier) | Open metadata via Crossref. [Source.](https://www.crossref.org/) |

MapForge does **not** redistribute any data from these sources — it only
uses their identifier strings as keys when resolving against your target
graph. If your target graph contains data sourced from one of these, that
data's license is the upstream's.

## When you ingest your own source

The reference-data section above is informational — when you ingest *your
own* source through MapForge, the resulting nodes and relationships carry
no license metadata from MapForge. Your data is your data. The
`source_origin` property automatically added to MERGE'd nodes is set to
`"contributor"` for contributor bundles; you can use it (or a custom
property added via `mapping.yaml`) to attach license tags to nodes you
write.

## Cross-references

- Citation file: [`CITATION.cff`](../CITATION.cff)
- Disbiome example: [`examples/disbiome/README.md`](../../examples/disbiome/README.md)
- Provenance metadata stored alongside data: [`provenance.md`](provenance.md)
