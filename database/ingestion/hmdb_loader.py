"""
HMDB (Human Metabolome Database) Loader

Loads metabolite data from HMDB XML into Neo4j.
Special focus on microbial metabolites, SCFAs, and metabolites
relevant to gut-host interactions.

Data source: https://www.hmdb.ca/downloads
"""

from typing import Optional, Iterator, Dict, Any, List
from .base_loader import FileBasedLoader, normalize_disease_name, generate_disease_id
from database.ingestion.utils import resolve_compound_id
import xml.etree.ElementTree as ET
import gzip
import logging

logger = logging.getLogger(__name__)

# HMDB XML namespace
HMDB_NS = "{http://www.hmdb.ca}"

# SCFAs and their chain lengths.
# Matched EXACTLY (see match_scfa) against a compound's name / IUPAC name /
# synonyms. These were previously substring-matched (`if scfa_name in
# name_lower`), which flagged 1,177 compounds as SCFAs — pesticides
# (Fenvalerate), opioids (Codeine, acetate), solvents (Ethyl acetate), salts,
# a steroid and a nucleotide — when ~8 are real. See #277.
SCFA_NAMES = {
    "acetate": 2, "acetic acid": 2,
    "propionate": 3, "propionic acid": 3,
    "butyrate": 4, "butyric acid": 4, "butanoate": 4, "butanoic acid": 4,
    "valerate": 5, "valeric acid": 5, "pentanoate": 5, "pentanoic acid": 5,
    "caproate": 6, "hexanoate": 6, "caproic acid": 6, "hexanoic acid": 6,
    "isobutyrate": 4, "isobutyric acid": 4,
    "isovalerate": 5, "isovaleric acid": 5,
}

# ClassyFire *class* values (HMDB <taxonomy><class>) that imply microbial
# relevance. Kept deliberately small: the previous list mixed real ClassyFire
# classes with individual compound names ("trimethylamine", "p-cresol",
# "skatole", "hippuric acid"), which are never <class> values — only "phenols"
# ever matched anything, admitting 421 of the 1,598 loaded compounds. #277
MICROBIAL_METABOLITE_CLASSES = {
    "phenols",
    "indoles and derivatives",
    "bile acids, alcohols and derivatives",
}


def _curated_microbial_names() -> frozenset:
    """The curated microbial metabolites, sourced from the PRODUCES data.

    HMDB itself carries NO microbial signal — verified against the real
    hmdb_metabolites.xml (2026-07-15): "Microbial" appears nowhere in the
    ontology for butyric acid, lactic acid, ethanol or riboflavin; the only
    source terms are Endogenous / Food / Plant / Drug. So the microbial subset
    cannot be derived from HMDB and must come from what we actually claim
    microbes produce.

    Derived from produces_loader's TAXON_METABOLITE_PRODUCERS rather than
    duplicated, so adding a metabolite there automatically pulls its HMDB
    entry in. A second hardcoded list is exactly the rot that caused #269,
    #271 and #272.
    """
    from database.ingestion.produces_loader import TAXON_METABOLITE_PRODUCERS

    return frozenset(
        metabolite[0].strip().lower()
        for metabolites in TAXON_METABOLITE_PRODUCERS.values()
        for metabolite in metabolites
    )


CURATED_MICROBIAL_NAMES = _curated_microbial_names()


def _normalized(names) -> set:
    """Lowercase/strip a list of candidate names, dropping blanks."""
    return {n.strip().lower() for n in names if n and n.strip()}


def match_scfa(names) -> Optional[int]:
    """Return the carbon chain length if any name is EXACTLY an SCFA, else None.

    Exactness is the whole point — see SCFA_NAMES. #277

    Pass ONLY the display name and IUPAC name here, never synonyms. HMDB's
    synonym lists are not clean identity assertions: they include the acyl
    moiety of conjugates, so `Acetylglycine` (HMDB0000532) lists "Acetate" and
    "Acetic acid", and `Hexanoylcarnitine` (HMDB0000756) lists "Hexanoate" and
    "Hexanoic acid". Matching those would re-introduce exactly the kind of
    false positive #277 exists to remove — verified against the real
    hmdb_metabolites.xml, and it costs nothing, because every genuine SCFA
    carries the acid form as its HMDB display name ("Butyric acid", …).
    """
    for candidate in _normalized(names):
        if candidate in SCFA_NAMES:
            return SCFA_NAMES[candidate]
    return None


def is_curated_microbial(names) -> bool:
    """True if any name is EXACTLY a curated microbial metabolite.

    Exact for the same reason as match_scfa: 'Indole-3-carbinol' is not
    'Indole'. Synonyms carry this — 'Butyrate' and 'Lactate' are real HMDB
    synonyms of Butyric acid / L-Lactic acid, which is what lets the curated
    short names reach their HMDB entries. #277 / #276
    """
    return bool(_normalized(names) & CURATED_MICROBIAL_NAMES)


def backfill_scfa_flags(driver, database: str) -> int:
    """Clear is_scfa/carbon_chain_length left true by the #277 substring bug.

    Re-running the loader cannot repair this: the false positives are now
    excluded by the fixed filter, so they are never visited again and keep a
    stale `is_scfa = true`. MERGE/SET only writes what it is given; it never
    unsets. So the repair has to be explicit.

    Matches on name/IUPAC only — existing nodes predate synonym loading — which
    is sufficient, because every genuine SCFA's HMDB display name is already an
    exact SCFA_NAMES entry ("Butyric acid", "Acetic acid", …).

    Returns the number of nodes cleared. Idempotent.
    """
    cypher = """
    MATCH (c:Compound)
    WHERE c.is_scfa = true
      AND NOT toLower(c.name) IN $scfa_names
      AND NOT toLower(coalesce(c.iupac_name, '')) IN $scfa_names
    SET c.is_scfa = false,
        c.carbon_chain_length = null
    RETURN count(c) AS cleared
    """
    with driver.session(database=database) as session:
        record = session.run(cypher, scfa_names=sorted(SCFA_NAMES)).single()
        cleared = record["cleared"] if record else 0

    logger.info(
        "Cleared stale is_scfa on %d compound(s) in '%s' (#277)", cleared, database
    )
    return cleared


class HMDBLoader(FileBasedLoader):
    """
    Load HMDB metabolite data into Neo4j.

    Creates:
    - Metabolite nodes with comprehensive chemical and biological properties
    - Disease associations
    - Pathway participation
    - Protein interactions (enzymes)

    Special focus on:
    - Microbial metabolites (is_microbial flag)
    - SCFAs (is_scfa flag with carbon chain length)
    - Biomarker information
    """

    @property
    def source_name(self) -> str:
        return "HMDB"

    def __init__(
        self,
        driver,
        organization_id: str,
        file_path: str,
        batch_size: int = 500,
        database: str = "neo4j",
        filter_to_microbial: bool = False
    ):
        """
        Initialize the HMDB loader.

        Args:
            driver: Neo4j driver
            organization_id: Organization ID
            file_path: Path to hmdb_metabolites.xml or .xml.gz
            batch_size: Records per batch
            database: Neo4j database name
            filter_to_microbial: Only load microbial-related metabolites
        """
        super().__init__(driver, organization_id, file_path, batch_size, database)
        self.filter_to_microbial = filter_to_microbial

    def extract(self, **kwargs) -> Iterator[Dict[str, Any]]:
        """
        Extract metabolite data from HMDB XML.

        Uses iterparse for memory efficiency.
        """
        logger.info(f"Loading HMDB data from {self.file_path}")

        # Open file. Both gzip.open and the builtin open are context managers,
        # so a single `with` covers both branches.
        opener = gzip.open if self.file_path.endswith(".gz") else open
        with opener(self.file_path, "rb") as file_handle:
            context = ET.iterparse(file_handle, events=("end",))
            count = 0

            for event, elem in context:
                if elem.tag == f"{HMDB_NS}metabolite":
                    metabolite_data = self._parse_metabolite_element(elem)

                    if metabolite_data:
                        # Apply filter if needed
                        if self.filter_to_microbial:
                            if not (metabolite_data.get("is_microbial") or
                                    metabolite_data.get("is_scfa")):
                                elem.clear()
                                continue

                        yield metabolite_data
                        count += 1

                        if count % 10000 == 0:
                            logger.info(f"Extracted {count} metabolites...")

                    elem.clear()

    def _parse_metabolite_element(self, elem) -> Optional[Dict[str, Any]]:
        """Parse a single metabolite element from XML."""
        try:
            hmdb_id = self._get_text(elem, f"{HMDB_NS}accession")
            if not hmdb_id:
                return None

            name = self._get_text(elem, f"{HMDB_NS}name")
            if not name:
                return None

            # Chemical identifiers
            iupac_name = self._get_text(elem, f"{HMDB_NS}iupac_name")
            chemical_formula = self._get_text(elem, f"{HMDB_NS}chemical_formula")
            monoisotopic_mass = self._safe_float(self._get_text(elem, f"{HMDB_NS}monisotopic_molecular_weight"))
            average_mass = self._safe_float(self._get_text(elem, f"{HMDB_NS}average_molecular_weight"))

            # Structure
            smiles = self._get_text(elem, f"{HMDB_NS}smiles")
            inchi = self._get_text(elem, f"{HMDB_NS}inchi")
            inchi_key = self._get_text(elem, f"{HMDB_NS}inchikey")

            # External IDs
            # HMDB's evidence tier: quantified > detected > expected > predicted.
            # Load-bearing for #276: HMDB carries near-duplicate entries where a
            # *predicted* stub holds the plainer name. e.g. HMDB0304356 "formate"
            # is `expected` with 0 diseases / 0 pathways / no KEGG id, while
            # HMDB0000142 "Formic acid" is `quantified` with 14 diseases and 39
            # pathways and lists "Formate" only as a synonym. Resolving on name
            # alone would attach formate's 4,376 producers to the empty stub.
            hmdb_status = self._get_text(elem, f"{HMDB_NS}status")

            cas_number = self._get_text(elem, f"{HMDB_NS}cas_registry_number")
            kegg_id = self._get_text(elem, f"{HMDB_NS}kegg_id")
            chebi_id = self._get_text(elem, f"{HMDB_NS}chebi_id")
            pubchem_cid = self._get_text(elem, f"{HMDB_NS}pubchem_compound_id")
            drugbank_id = self._get_text(elem, f"{HMDB_NS}drugbank_id")
            metlin_id = self._get_text(elem, f"{HMDB_NS}metlin_id")

            # Taxonomy
            taxonomy = elem.find(f"{HMDB_NS}taxonomy")
            if taxonomy is not None:
                kingdom = self._get_text(taxonomy, f"{HMDB_NS}kingdom")
                super_class = self._get_text(taxonomy, f"{HMDB_NS}super_class")
                class_ = self._get_text(taxonomy, f"{HMDB_NS}class")
                sub_class = self._get_text(taxonomy, f"{HMDB_NS}sub_class")
                direct_parent = self._get_text(taxonomy, f"{HMDB_NS}direct_parent")
            else:
                kingdom = super_class = class_ = sub_class = direct_parent = None

            # Biological properties
            description = self._get_text(elem, f"{HMDB_NS}description")

            # Ontology - determine if endogenous/microbial
            ontology = elem.find(f"{HMDB_NS}ontology")
            origins = []
            biofunctions = []
            if ontology is not None:
                for term in ontology.findall(f".//{HMDB_NS}term"):
                    term_text = term.text.lower() if term.text else ""
                    if "origin" in term_text:
                        origins.append(term_text)
                    if "function" in term_text or "role" in term_text:
                        biofunctions.append(term_text)

            # Synonyms. HMDB carries a rich synonym list (Butyric acid has 43,
            # incl. "Butyrate"; L-Lactic acid has 58, incl. "Lactate") and it is
            # the only bridge from our curated short names to HMDB entries.
            # Previously parsed not at all. #277 / #276
            synonyms_elem = elem.find(f"{HMDB_NS}synonyms")
            synonyms = []
            if synonyms_elem is not None:
                synonyms = [
                    s.text.strip()
                    for s in synonyms_elem.findall(f"{HMDB_NS}synonym")
                    if s.text and s.text.strip()
                ]

            # Every name this compound is known by. Note the two uses differ
            # deliberately (see match_scfa): SCFA detection must NOT consider
            # synonyms, because HMDB lists a conjugate's acyl moiety among them.
            canonical_names = [name, iupac_name]
            all_names = [name, iupac_name, *synonyms]

            # Check if endogenous.
            # NOTE: `origins` is effectively always empty — it collects only
            # ontology terms whose TEXT contains "origin", but HMDB's terms are
            # values like "Endogenous"/"Food"/"Plant". Left as-is rather than
            # silently changing this flag's meaning; it is not used for
            # filtering. See #277 for the full picture.
            is_endogenous = any("endogenous" in o for o in origins)

            # SCFA: EXACT match, name/IUPAC only, never substring, never synonyms. #277
            carbon_chain_length = match_scfa(canonical_names)
            is_scfa = carbon_chain_length is not None

            # Microbial relevance. Driven by the curated producer list, because
            # HMDB has no microbial signal of its own (see _curated_microbial_names).
            # Synonyms ARE used here — they're the only bridge from our curated
            # short names ("Butyrate", "Lactate") to HMDB's acid-form entries.
            # The acyl-moiety noise that rules synonyms out for match_scfa is
            # tolerable for inclusion: worst case a conjugate like Acetylglycine
            # is also loaded, which is a real metabolite, not a wrong flag.
            # is_scfa is deliberately NOT forced into this: conflating the two is
            # what let the substring bug widen from a bad flag into a bad subset.
            is_microbial = is_curated_microbial(all_names) or bool(
                class_ and class_.lower() in MICROBIAL_METABOLITE_CLASSES
            )

            # Biological fluids/locations
            biofluid_locations = [
                bf.text for bf in elem.findall(f"{HMDB_NS}biological_properties/{HMDB_NS}biospecimen_locations/{HMDB_NS}biospecimen")
                if bf.text
            ]

            # Tissue locations
            tissue_locations = [
                t.text for t in elem.findall(f"{HMDB_NS}biological_properties/{HMDB_NS}tissue_locations/{HMDB_NS}tissue")
                if t.text
            ]

            # Pathways
            pathways = []
            for pathway in elem.findall(f"{HMDB_NS}biological_properties/{HMDB_NS}pathways/{HMDB_NS}pathway"):
                pw_name = self._get_text(pathway, f"{HMDB_NS}name")
                pw_kegg = self._get_text(pathway, f"{HMDB_NS}kegg_map_id")
                pw_smpdb = self._get_text(pathway, f"{HMDB_NS}smpdb_id")
                if pw_name:
                    pathways.append({
                        "name": pw_name,
                        "kegg_id": pw_kegg,
                        "smpdb_id": pw_smpdb
                    })

            # Disease associations
            diseases = []
            for disease in elem.findall(f"{HMDB_NS}diseases/{HMDB_NS}disease"):
                d_name = self._get_text(disease, f"{HMDB_NS}name")
                d_omim = self._get_text(disease, f"{HMDB_NS}omim_id")
                if d_name:
                    diseases.append({
                        "name": d_name,
                        "omim_id": d_omim
                    })

            # Enzymes that process this metabolite
            proteins = []
            for protein in elem.findall(f"{HMDB_NS}protein_associations/{HMDB_NS}protein"):
                p_name = self._get_text(protein, f"{HMDB_NS}name")
                p_uniprot = self._get_text(protein, f"{HMDB_NS}uniprot_id")
                p_gene = self._get_text(protein, f"{HMDB_NS}gene_name")
                if p_uniprot:
                    proteins.append({
                        "name": p_name,
                        "uniprot_id": p_uniprot,
                        "gene_name": p_gene
                    })

            # Concentrations (normal/abnormal)
            concentrations = []
            for conc in elem.findall(f"{HMDB_NS}normal_concentrations/{HMDB_NS}concentration"):
                biofluid = self._get_text(conc, f"{HMDB_NS}biofluid")
                value = self._get_text(conc, f"{HMDB_NS}concentration_value")
                units = self._get_text(conc, f"{HMDB_NS}concentration_units")
                if biofluid and value:
                    concentrations.append({
                        "biofluid": biofluid,
                        "value": value,
                        "units": units,
                        "type": "normal"
                    })

            return {
                "hmdb_id": hmdb_id,
                "name": name,
                "iupac_name": iupac_name,
                "synonyms": synonyms,
                "hmdb_status": hmdb_status,
                "description": description,

                # Chemical properties
                "chemical_formula": chemical_formula,
                "molecular_weight": average_mass,
                "monoisotopic_mass": monoisotopic_mass,
                "smiles": smiles,
                "inchi": inchi,
                "inchi_key": inchi_key,

                # External IDs
                "cas_number": cas_number,
                "kegg_id": kegg_id,
                "chebi_id": chebi_id,
                "pubchem_cid": pubchem_cid,
                "drugbank_id": drugbank_id,
                "metlin_id": metlin_id,

                # Taxonomy
                "kingdom": kingdom,
                "super_class": super_class,
                "class": class_,
                "sub_class": sub_class,
                "direct_parent": direct_parent,

                # Biological classification
                "is_endogenous": is_endogenous,
                "is_microbial": is_microbial,
                "is_scfa": is_scfa,
                "carbon_chain_length": carbon_chain_length,
                "is_drug": drugbank_id is not None,

                # Locations
                "biofluid_locations": biofluid_locations,
                "tissue_locations": tissue_locations,

                # Associations
                "_pathways": pathways,
                "_diseases": diseases,
                "_proteins": proteins,
                "_concentrations": concentrations,
            }

        except Exception as e:
            logger.warning(f"Failed to parse metabolite element: {e}")
            return None

    def _get_text(self, elem, path: str) -> Optional[str]:
        """Get text from element path."""
        child = elem.find(path)
        return child.text.strip() if child is not None and child.text else None

    def _safe_float(self, value: Optional[str]) -> Optional[float]:
        """Convert to float safely."""
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    def transform(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Transform HMDB record for Neo4j loading."""
        hmdb_id = record.get("hmdb_id")
        if not hmdb_id:
            return None

        try:
            compound_id = resolve_compound_id(
                record.get("inchi_key"),
                record.get("pubchem_cid"),
            )
        except ValueError:
            return None

        return {
            "compound_id": compound_id,
            "hmdb_id": hmdb_id,
            "name": record.get("name"),
            "iupac_name": record.get("iupac_name"),
            # Persisted so produces_loader can resolve curated short names
            # ("Butyrate") onto the right HMDB entry ("Butyric acid"). #276
            "synonyms": record.get("synonyms") or [],
            # Evidence tier — used to prefer a real entry over a predicted stub
            # that happens to hold the plainer name. #276
            "hmdb_status": record.get("hmdb_status"),
            "description": record.get("description"),

            # Chemical
            "chemical_formula": record.get("chemical_formula"),
            "molecular_weight": record.get("molecular_weight"),
            "monoisotopic_mass": record.get("monoisotopic_mass"),
            "smiles": record.get("smiles"),
            "inchi": record.get("inchi"),
            "inchi_key": record.get("inchi_key"),

            # External IDs
            "cas_number": record.get("cas_number"),
            "kegg_id": record.get("kegg_id"),
            "chebi_id": record.get("chebi_id"),
            "pubchem_cid": record.get("pubchem_cid"),
            "drugbank_id": record.get("drugbank_id"),
            "metlin_id": record.get("metlin_id"),

            # Classification
            "kingdom": record.get("kingdom"),
            "super_class": record.get("super_class"),
            "class": record.get("class"),
            "sub_class": record.get("sub_class"),
            "direct_parent": record.get("direct_parent"),

            # Microbiome relevance
            "is_endogenous": record.get("is_endogenous", False),
            "is_microbial": record.get("is_microbial", False),
            "is_scfa": record.get("is_scfa", False),
            "carbon_chain_length": record.get("carbon_chain_length"),
            "is_drug": record.get("is_drug", False),
            "is_food_component": "food" in (record.get("description") or "").lower(),

            # Locations
            "biofluid_locations": record.get("biofluid_locations", []),
            "tissue_locations": record.get("tissue_locations", []),

            # Related entities
            "_pathways": record.get("_pathways", []),
            "_diseases": record.get("_diseases", []),
            "_proteins": record.get("_proteins", []),

            # Multi-tenant
            "organization_id": self.organization_id,
        }

    def load_batch(self, batch: List[Dict[str, Any]]) -> Dict[str, int]:
        """Load HMDB batch into Neo4j."""
        if not batch:
            return {"nodes_created": 0, "relationships_created": 0}

        nodes_created = 0
        rels_created = 0

        # Load compound nodes
        nodes_created += self._load_compounds(batch)

        # Load pathway relationships
        for record in batch:
            for pathway in record.get("_pathways", []):
                rels_created += self._load_pathway_relationship(record["compound_id"], pathway)

            for disease in record.get("_diseases", []):
                nodes_created += self._ensure_disease_exists(disease)
                rels_created += self._load_disease_relationship(record["compound_id"], disease)

            for protein in record.get("_proteins", []):
                nodes_created += self._ensure_protein_exists(protein)
                rels_created += self._load_protein_relationship(record["compound_id"], protein)

        return {
            "nodes_created": nodes_created,
            "relationships_created": rels_created
        }

    def _load_compounds(self, batch: List[Dict[str, Any]]) -> int:
        """Load Compound nodes."""
        compound_records = []
        for record in batch:
            m = {k: v for k, v in record.items() if not k.startswith("_")}
            compound_records.append(m)

        query = """
            UNWIND $compounds AS m
            MERGE (met:Compound {compound_id: m.compound_id})
            ON CREATE SET
                met.hmdb_id = m.hmdb_id,
                met.name = m.name,
                met.iupac_name = m.iupac_name,
                // Read by produces_loader._resolve_canonical_compound (#276):
                // synonyms are the only bridge from a curated short name
                // ("Butyrate") to HMDB's acid-form entry, and hmdb_status is
                // what stops a predicted stub outranking a real entry.
                met.synonyms = m.synonyms,
                met.hmdb_status = m.hmdb_status,
                met.description = m.description,
                met.chemical_formula = m.chemical_formula,
                met.molecular_weight = m.molecular_weight,
                met.monoisotopic_mass = m.monoisotopic_mass,
                met.smiles = m.smiles,
                met.inchi = m.inchi,
                met.inchi_key = m.inchi_key,
                met.cas_number = m.cas_number,
                met.kegg_id = m.kegg_id,
                met.chebi_id = m.chebi_id,
                met.pubchem_cid = m.pubchem_cid,
                met.drugbank_id = m.drugbank_id,
                met.metlin_id = m.metlin_id,
                met.kingdom = m.kingdom,
                met.super_class = m.super_class,
                met.class = m.class,
                met.sub_class = m.sub_class,
                met.direct_parent = m.direct_parent,
                met.is_endogenous = m.is_endogenous,
                met.is_microbial = m.is_microbial,
                met.is_scfa = m.is_scfa,
                met.carbon_chain_length = m.carbon_chain_length,
                met.is_drug = m.is_drug,
                met.is_food_component = m.is_food_component,
                met.biofluid_locations = m.biofluid_locations,
                met.tissue_locations = m.tissue_locations,
                met.organization_id = m.organization_id,
                met.created_at = datetime()
            ON MATCH SET
                met.updated_at = datetime(),
                // Backfill on re-run. These two were added after the first HMDB
                // load, so every already-loaded compound has them null; without
                // this an idempotent re-run reports nodes_updated: 0 and changes
                // nothing, leaving #276's resolver permanently blind. Safe to
                // re-assert: both are HMDB-owned, never curated by hand.
                met.synonyms = m.synonyms,
                met.hmdb_status = m.hmdb_status
            RETURN count(met) AS count
        """

        result = self.execute_cypher(query, {"compounds": compound_records})
        return result[0]["count"] if result else 0

    def _load_pathway_relationship(self, compound_id: str, pathway: Dict[str, Any]) -> int:
        """Load compound-pathway relationship."""
        kegg_id = pathway.get("kegg_id")
        smpdb_id = pathway.get("smpdb_id")

        if not kegg_id and not smpdb_id:
            return 0

        pathway_id = f"KEGG:{kegg_id}" if kegg_id else f"SMPDB:{smpdb_id}"

        query = """
            MERGE (p:Pathway {pathway_id: $pathway_id})
            ON CREATE SET
                p.name = $name,
                p.kegg_id = $kegg_id,
                p.smpdb_id = $smpdb_id,
                p.organization_id = $org_id,
                p.created_at = datetime()
            WITH p
            MATCH (m:Compound {compound_id: $compound_id})
            MERGE (m)-[r:PARTICIPATES_IN]->(p)
            ON CREATE SET r.created_at = datetime()
            RETURN count(r) AS count
        """

        result = self.execute_cypher(query, {
            "pathway_id": pathway_id,
            "name": pathway.get("name"),
            "kegg_id": kegg_id,
            "smpdb_id": smpdb_id,
            "compound_id": compound_id,
            "org_id": self.organization_id,
        })

        return result[0]["count"] if result else 0

    def _ensure_disease_exists(self, disease: Dict[str, Any]) -> int:
        """
        Ensure disease node exists using normalized name as MERGE key.

        This ensures that HMDB diseases merge with Disbiome diseases that
        have the same name (case-insensitive), solving the entity resolution
        problem that previously created duplicate Disease nodes.
        """
        name = disease.get("name")
        if not name:
            return 0

        # Normalize disease name for consistent entity resolution
        name_normalized = normalize_disease_name(name)
        if not name_normalized:
            return 0

        omim_id = disease.get("omim_id")
        disease_id = generate_disease_id(name, {'omim_id': omim_id})

        query = """
            MERGE (d:Disease {name_normalized: $name_normalized})
            ON CREATE SET
                d.name = $name,
                d.disease_id = $disease_id,
                d.omim_id = $omim_id,
                d.organization_id = $org_id,
                d.sources = ['hmdb'],
                d.created_at = datetime()
            ON MATCH SET
                d.omim_id = COALESCE(d.omim_id, $omim_id),
                d.sources = CASE
                    WHEN 'hmdb' IN d.sources THEN d.sources
                    ELSE d.sources + 'hmdb'
                END,
                d.updated_at = datetime()
            RETURN count(d) AS count
        """

        result = self.execute_cypher(query, {
            "name_normalized": name_normalized,
            "name": name,
            "disease_id": disease_id,
            "omim_id": omim_id,
            "org_id": self.organization_id,
        })

        return result[0]["count"] if result else 0

    def _load_disease_relationship(self, compound_id: str, disease: Dict[str, Any]) -> int:
        """Load compound-disease association using normalized disease name."""
        name = disease.get("name")
        if not name:
            return 0

        name_normalized = normalize_disease_name(name)
        if not name_normalized:
            return 0

        query = """
            MATCH (m:Compound {compound_id: $compound_id})
            MATCH (d:Disease {name_normalized: $name_normalized})
            MERGE (m)-[r:LINKED_TO_DISEASE]->(d)
            ON CREATE SET
                r.evidence_source = 'HMDB',
                r.evidence_level = 'curated',
                r.sources = ['hmdb'],
                r.created_at = datetime()
            ON MATCH SET
                r.sources = CASE
                    WHEN 'hmdb' IN r.sources THEN r.sources
                    ELSE r.sources + 'hmdb'
                END,
                r.updated_at = datetime()
            RETURN count(r) AS count
        """

        result = self.execute_cypher(query, {
            "compound_id": compound_id,
            "name_normalized": name_normalized,
        })

        return result[0]["count"] if result else 0

    def _ensure_protein_exists(self, protein: Dict[str, Any]) -> int:
        """Ensure protein node exists."""
        uniprot_id = protein.get("uniprot_id")
        if not uniprot_id:
            return 0

        query = """
            MERGE (p:Protein {protein_id: $protein_id})
            ON CREATE SET
                p.uniprot_id = $uniprot_id,
                p.name = $name,
                p.gene_name = $gene_name,
                p.organization_id = $org_id,
                p.created_at = datetime()
            RETURN count(p) AS count
        """

        result = self.execute_cypher(query, {
            "protein_id": f"UniProt:{uniprot_id}",
            "uniprot_id": uniprot_id,
            "name": protein.get("name"),
            "gene_name": protein.get("gene_name"),
            "org_id": self.organization_id,
        })

        return result[0]["count"] if result else 0

    def _load_protein_relationship(self, compound_id: str, protein: Dict[str, Any]) -> int:
        """Load compound-protein relationship (enzyme processes compound)."""
        uniprot_id = protein.get("uniprot_id")
        if not uniprot_id:
            return 0

        query = """
            MATCH (m:Compound {compound_id: $compound_id})
            MATCH (p:Protein {protein_id: $protein_id})
            MERGE (p)-[r:PROCESSES]->(m)
            ON CREATE SET
                r.evidence_source = 'HMDB',
                r.created_at = datetime()
            RETURN count(r) AS count
        """

        result = self.execute_cypher(query, {
            "compound_id": compound_id,
            "protein_id": f"UniProt:{uniprot_id}",
        })

        return result[0]["count"] if result else 0


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    from neo4j import GraphDatabase

    load_dotenv()

    NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
    NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

    hmdb_file = os.getenv(
        "HMDB_FILE",
        os.path.join(os.path.dirname(__file__), "..", "..", "data", "hmdb", "hmdb_metabolites.xml")
    )

    if not os.path.exists(hmdb_file):
        print(f"HMDB file not found: {hmdb_file}")
        print("Please download from https://www.hmdb.ca/downloads")
        exit(1)

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    try:
        loader = HMDBLoader(
            driver=driver,
            organization_id="default",
            file_path=hmdb_file,
            filter_to_microbial=True  # Only microbial metabolites
        )

        stats = loader.run()
        print(f"Loaded HMDB data: {stats.to_dict()}")
    finally:
        driver.close()
