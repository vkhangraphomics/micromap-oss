# MicroMap format

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.22779109.svg)](https://doi.org/10.5281/zenodo.22779109)
[![KG-Registry](https://img.shields.io/badge/KG--Registry-listed-blue)](https://kghub.org/kg-registry/resource/micromap/micromap.html)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

The open format of [MicroMap](https://www.graphomics.com/knowledge), Graphomics' microbiome
knowledge graph. This repository holds:

- **[The graph schema](spec/graph-schema.md):** node labels, identifiers, relationship types and
  provenance properties. Use it to produce data in the same shape, read MicroMap exports, or link
  your own graph to MicroMap by shared identifiers.
- **[The decision chain format](spec/decision-chain.md)** and its reference verifier, so anyone
  can check an exported decision log independently.

The graph's data, and the software that builds and serves it, are not part of this repository.
Access to MicroMap is provisioned per organization: see [graphomics.com](https://www.graphomics.com).

> **Not to be confused with** the "MicroMap" microbiome-metabolism network visualization resource
> from the Thiele lab (University of Galway), published in
> [*npj Biofilms and Microbiomes*](https://www.nature.com/articles/s41522-025-00853-0) (2025).
> Unrelated projects with the same name.

## Verify a decision log

Python 3.10+, no dependencies:

```bash
python -m micromap_format.verify_chain export.jsonl --expect-head sha256:...
```

Run the tests with `pip install pytest && pytest`.

## Earlier versions

Releases up to v0.1.4 published the full MicroMap platform code. From v0.2.0 this repository
publishes the format only.

## Citing MicroMap

See [CITATION.cff](CITATION.cff), or cite the DOI
[10.5281/zenodo.22779109](https://doi.org/10.5281/zenodo.22779109).

## License

MIT. See [LICENSE](LICENSE).
