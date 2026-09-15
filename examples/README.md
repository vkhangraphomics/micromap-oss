# MicroMap examples

Curated end-to-end example bundles for the MapForge ingestion pipeline.

| Example | Source shape | Scale | What it demonstrates |
|---|---|---|---|
| [disbiome/](disbiome/) | Disbiome CSV (microbiome–disease associations) | 50 rows, 32 organisms, 24 diseases | Full `inspect → map → resolve → plan → emit → submit → approve` flow against a populated dev KG; resolution against NCBI Taxonomy and Disease Ontology; partner-tier routing to `micromap-core`. |

Each example ships with `mapping.yaml`, `routing-policy.yaml`,
`contributor.yaml`, and a README walking through the pipeline. Add new
examples here when bringing on a new source shape or a new
contributor-tier policy.
