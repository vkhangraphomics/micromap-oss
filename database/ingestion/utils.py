from __future__ import annotations


def resolve_compound_id(
    inchi_key: str | None,
    pubchem_cid: str | int | None,
) -> str:
    """Return a prefixed CURIE for merging Compound nodes.

    Prefers InChIKey (structure-based, source-agnostic) over PubChem CID.
    Raises ValueError if neither is available — callers must log and skip.
    """
    if inchi_key:
        return f"INCHIKEY:{inchi_key}"
    if pubchem_cid:
        return f"PUBCHEM:{pubchem_cid}"
    raise ValueError(
        "compound requires at least one of inchi_key or pubchem_cid"
    )
