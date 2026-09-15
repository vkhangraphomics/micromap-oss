"""
NCBI Taxonomy Data Loader

Loads microbial taxonomy data from NCBI Taxonomy database into Neo4j.
Focuses on bacteria, archaea, and fungi relevant to human microbiome.

Data source: https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/
"""

from typing import Optional, Iterator, Dict, Any, List, Set
from .base_loader import FileBasedLoader
from array import array
import gzip
import os
import logging

logger = logging.getLogger(__name__)


# Taxonomic ranks of interest for microbiome analysis
RELEVANT_RANKS = {
    "superkingdom", "kingdom", "phylum", "class", "order",
    "family", "genus", "species", "strain", "subspecies"
}

# Taxa groups relevant to human microbiome
MICROBIOME_KINGDOMS = {"Bacteria", "Archaea", "Fungi", "Viruses"}


class NCBITaxonomyLoader(FileBasedLoader):
    """
    Load NCBI Taxonomy data into Neo4j.

    Expects the taxdump directory containing:
    - nodes.dmp: Taxonomy hierarchy
    - names.dmp: Scientific names
    - merged.dmp: Merged tax IDs (optional)
    - delnodes.dmp: Deleted tax IDs (optional)
    """

    @property
    def source_name(self) -> str:
        return "NCBI Taxonomy"

    def __init__(
        self,
        driver,
        organization_id: str,
        taxdump_dir: str,
        batch_size: int = 5000,
        database: str = "neo4j",
        filter_to_microbiome: bool = True
    ):
        """
        Initialize the NCBI Taxonomy loader.

        Args:
            driver: Neo4j driver
            organization_id: Organization ID for multi-tenant isolation
            taxdump_dir: Directory containing taxdump files
            batch_size: Records per batch
            database: Neo4j database name
            filter_to_microbiome: Only load microbiome-relevant taxa
        """
        super().__init__(driver, organization_id, taxdump_dir, batch_size, database)
        self.taxdump_dir = taxdump_dir
        self.filter_to_microbiome = filter_to_microbiome

        self._init_caches()

    def _init_caches(self):
        """Dense, integer-indexed caches (#273).

        The taxonomy is ~2.9M taxa and this runs in a 512 MB container. Holding
        a dict per taxon (`self._nodes[tax_id] = {...}`) meant ~2.9M inner dict
        objects and OOM-killed the loader. String-keyed dicts cannot fit at this
        scale either — 2.9M entries is ~290 MB of dict overhead before values,
        plus ~160 MB of tax_id key strings.

        Arrays indexed by integer tax_id cost ~4 bytes per taxon instead, and
        the low-cardinality columns (rank, division, genetic code — a few dozen
        distinct values across millions of rows) are stored as codes into a
        shared vocabulary, so each distinct string exists exactly once.
        """
        self._max_id = 0
        self._parent = array("i")          # parent tax_id, 0 = none
        self._rank_code = array("h")       # index into _rank_vocab, 0 = absent
        self._division_code = array("h")
        self._gencode_code = array("h")
        # index 0 is the "absent" sentinel in every vocabulary
        self._rank_vocab: List[Optional[str]] = [None]
        self._division_vocab: List[Optional[str]] = [None]
        self._gencode_vocab: List[Optional[str]] = [None]
        self._names: Dict[int, str] = {}
        self._common_names: Dict[int, str] = {}
        self._included: bytearray = bytearray()

    @staticmethod
    def _code_for(value: Optional[str], vocab: List[Optional[str]],
                  lookup: Dict[Optional[str], int]) -> int:
        """Intern `value` into `vocab`, returning its code."""
        if value in lookup:
            return lookup[value]
        vocab.append(value)
        lookup[value] = len(vocab) - 1
        return lookup[value]

    def extract(self, **kwargs) -> Iterator[Dict[str, Any]]:
        """
        Extract taxonomy data from NCBI taxdump files.

        Yields:
            Taxonomy node records
        """
        logger.info(f"Loading NCBI Taxonomy from {self.taxdump_dir}")

        # Step 1: nodes.dmp -> dense arrays
        self._load_nodes_file()

        # Step 2: decide what is in scope BEFORE reading names in full, so the
        # name map only ever holds the taxa we will actually emit (#273).
        self._mark_included()

        # Step 3: names.dmp, restricted to included taxa
        self._load_names_file()

        # Step 4: Yield processed records
        for tax_id in range(1, self._max_id + 1):
            if not self._included[tax_id]:
                continue
            parent = self._parent[tax_id]
            yield {
                "tax_id": str(tax_id),
                "parent_tax_id": str(parent) if parent else None,
                "rank": self._rank_vocab[self._rank_code[tax_id]],
                "name": self._names.get(tax_id, ""),
                "common_name": self._common_names.get(tax_id),
                "division_id": self._division_vocab[self._division_code[tax_id]],
                "genetic_code_id": self._gencode_vocab[self._gencode_code[tax_id]],
            }

    def _load_nodes_file(self):
        """Load nodes.dmp file containing taxonomy hierarchy."""
        nodes_path = os.path.join(self.taxdump_dir, "nodes.dmp")

        if nodes_path.endswith(".gz"):
            opener = gzip.open
        else:
            opener = open

        # Two cheap passes over the file beat one expensive pass over RAM: the
        # first only learns how big the arrays need to be (#273).
        max_id = 0
        with opener(nodes_path, "rt") as f:
            for line in f:
                head = line.split("|", 1)[0].strip()
                if head.isdigit():
                    max_id = max(max_id, int(head))
        self._max_id = max_id

        n = max_id + 1
        self._parent = array("i", bytes(4 * n))
        self._rank_code = array("h", bytes(2 * n))
        self._division_code = array("h", bytes(2 * n))
        self._gencode_code = array("h", bytes(2 * n))
        self._included = bytearray(n)

        rank_lookup: Dict[Optional[str], int] = {None: 0}
        div_lookup: Dict[Optional[str], int] = {None: 0}
        gen_lookup: Dict[Optional[str], int] = {None: 0}

        count = 0
        with opener(nodes_path, "rt") as f:
            for line in f:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 3 and parts[0].isdigit():
                    tax_id = int(parts[0])
                    parent = int(parts[1]) if parts[1].isdigit() else 0
                    # NCBI makes the root its own parent; that is not an edge.
                    self._parent[tax_id] = 0 if parent == tax_id else parent
                    self._rank_code[tax_id] = self._code_for(
                        parts[2] or None, self._rank_vocab, rank_lookup)
                    self._division_code[tax_id] = self._code_for(
                        parts[4] if len(parts) > 4 else None,
                        self._division_vocab, div_lookup)
                    self._gencode_code[tax_id] = self._code_for(
                        parts[6] if len(parts) > 6 else None,
                        self._gencode_vocab, gen_lookup)
                    count += 1

        logger.info(f"Loaded {count} taxonomy nodes (max tax_id {max_id})")

    def _load_names_file(self):
        """Load names.dmp file containing taxon names."""
        names_path = os.path.join(self.taxdump_dir, "names.dmp")

        if names_path.endswith(".gz"):
            opener = gzip.open
        else:
            opener = open

        with opener(names_path, "rt") as f:
            for line in f:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 4 and parts[0].isdigit():
                    tax_id = int(parts[0])
                    # Only retain names for taxa we will emit. Keeping all 2.9M
                    # was a large part of the OOM (#273).
                    if tax_id > self._max_id or not self._included[tax_id]:
                        continue
                    name = parts[1]
                    name_class = parts[3]

                    if name_class == "scientific name":
                        self._names[tax_id] = name
                    elif name_class == "common name" and tax_id not in self._common_names:
                        self._common_names[tax_id] = name

        logger.info(f"Loaded names for {len(self._names)} taxa")

    def _scientific_name_ids(self, wanted: Set[str]) -> Set[int]:
        """tax_ids whose scientific name is in `wanted`.

        A targeted scan so the kingdom roots can be found without holding every
        name in memory (#273).
        """
        names_path = os.path.join(self.taxdump_dir, "names.dmp")
        opener = gzip.open if names_path.endswith(".gz") else open
        found: Set[int] = set()
        with opener(names_path, "rt") as f:
            for line in f:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 4 and parts[3] == "scientific name":
                    if parts[1] in wanted and parts[0].isdigit():
                        found.add(int(parts[0]))
        return found

    def _mark_included(self):
        """Mark which taxa to emit, in the `_included` bitmap.

        Replaces a downward BFS that first built a `children_map` dict-of-lists
        — one dict entry plus one list object per taxon, another large slice of
        the #273 OOM. Walking UP from each node and memoising the verdict along
        the chain needs no auxiliary per-taxon structure and settles each node
        once, amortised.
        """
        present = self._rank_code  # non-zero == the taxon existed in nodes.dmp

        if not self.filter_to_microbiome:
            for tax_id in range(1, self._max_id + 1):
                if present[tax_id]:
                    self._included[tax_id] = 1
            logger.info(f"Including all {sum(self._included)} taxa (unfiltered)")
            return

        roots = self._scientific_name_ids(MICROBIOME_KINGDOMS)
        logger.info(f"Found {len(roots)} microbiome kingdom roots")

        UNKNOWN, IN, OUT = 0, 1, 2
        state = bytearray(self._max_id + 1)
        for root in roots:
            if root <= self._max_id:
                state[root] = IN

        for tax_id in range(1, self._max_id + 1):
            if not present[tax_id] or state[tax_id] != UNKNOWN:
                continue
            chain: List[int] = []
            seen = set()
            cur = tax_id
            # Climb until a settled ancestor, the root (parent 0), or a cycle.
            while cur and cur <= self._max_id and state[cur] == UNKNOWN:
                if cur in seen:          # defensive: a malformed dump cycle
                    break
                seen.add(cur)
                chain.append(cur)
                cur = self._parent[cur]
            verdict = state[cur] if (cur and cur <= self._max_id) else OUT
            if verdict == UNKNOWN:
                verdict = OUT
            for node in chain:
                state[node] = verdict

        for tax_id in range(1, self._max_id + 1):
            if present[tax_id] and state[tax_id] == IN:
                self._included[tax_id] = 1

        logger.info(f"Identified {sum(self._included)} microbiome-relevant taxa")

    def transform(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Transform NCBI taxonomy record to graph format.

        Args:
            record: Raw taxonomy record

        Returns:
            Transformed record for Neo4j
        """
        tax_id = record.get("tax_id")
        if not tax_id:
            return None

        # Build hierarchy fields
        hierarchy = self._build_hierarchy(tax_id)

        return {
            "taxon_id": f"NCBITaxon:{tax_id}",
            "ncbi_tax_id": tax_id,
            "name": record.get("name", ""),
            "common_name": record.get("common_name"),
            "rank": record.get("rank"),
            "parent_taxon_id": f"NCBITaxon:{record['parent_tax_id']}" if record.get("parent_tax_id") else None,

            # Taxonomy hierarchy
            "kingdom": hierarchy.get("kingdom"),
            "phylum": hierarchy.get("phylum"),
            "class": hierarchy.get("class"),
            "order": hierarchy.get("order"),
            "family": hierarchy.get("family"),
            "genus": hierarchy.get("genus"),
            "species": hierarchy.get("species"),

            # Metadata
            "division_id": record.get("division_id"),
            "genetic_code_id": record.get("genetic_code_id"),
            "organization_id": self.organization_id,
        }

    def _build_hierarchy(self, tax_id: str) -> Dict[str, str]:
        """Build the full taxonomy hierarchy for a taxon."""
        hierarchy = {}
        current = int(tax_id) if str(tax_id).isdigit() else 0
        visited = set()

        while current and current not in visited:
            visited.add(current)
            if current > self._max_id or not self._rank_code[current]:
                break

            rank = self._rank_vocab[self._rank_code[current]]
            if rank in RELEVANT_RANKS:
                name = self._names.get(current)
                if name:
                    # Map rank names to our schema
                    if rank == "superkingdom":
                        hierarchy["kingdom"] = name
                    else:
                        hierarchy[rank] = name

            current = self._parent[current]

        return hierarchy

    def load_batch(self, batch: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Load a batch of taxonomy records into Neo4j.

        Args:
            batch: List of transformed taxonomy records

        Returns:
            Counts of nodes and relationships created
        """
        if not batch:
            return {"nodes_created": 0, "relationships_created": 0}

        # Create/update Taxon nodes
        nodes_count = self._load_taxon_nodes(batch)

        # Create parent-child relationships
        rels_count = self._load_parent_relationships(batch)

        return {
            "nodes_created": nodes_count,
            "relationships_created": rels_count
        }

    def _load_taxon_nodes(self, batch: List[Dict[str, Any]]) -> int:
        """Load Taxon nodes."""
        query = """
            UNWIND $batch AS record
            MERGE (t:Taxon {taxon_id: record.taxon_id})
            ON CREATE SET
                t.ncbi_tax_id = record.ncbi_tax_id,
                t.name = record.name,
                t.common_name = record.common_name,
                t.rank = record.rank,
                t.kingdom = record.kingdom,
                t.phylum = record.phylum,
                t.class = record.class,
                t.order = record.order,
                t.family = record.family,
                t.genus = record.genus,
                t.species = record.species,
                t.parent_taxon_id = record.parent_taxon_id,
                t.organization_id = record.organization_id,
                t.created_at = datetime()
            ON MATCH SET
                t.parent_taxon_id = record.parent_taxon_id,
                t.name = record.name,
                t.common_name = record.common_name,
                t.rank = record.rank,
                t.kingdom = record.kingdom,
                t.phylum = record.phylum,
                t.class = record.class,
                t.order = record.order,
                t.family = record.family,
                t.genus = record.genus,
                t.species = record.species,
                t.updated_at = datetime()
            RETURN count(t) AS count
        """

        result = self.execute_cypher(query, {"batch": batch})
        return result[0]["count"] if result else 0

    def _load_parent_relationships(self, batch: List[Dict[str, Any]]) -> int:
        """Load HAS_PARENT relationships."""
        # Filter to records with parent
        records_with_parent = [
            {"from_id": r["taxon_id"], "to_id": r["parent_taxon_id"]}
            for r in batch
            if r.get("parent_taxon_id")
        ]

        if not records_with_parent:
            return 0

        query = """
            UNWIND $records AS record
            MATCH (child:Taxon {taxon_id: record.from_id})
            MATCH (parent:Taxon {taxon_id: record.to_id})
            MERGE (child)-[r:HAS_PARENT]->(parent)
            RETURN count(r) AS count
        """

        result = self.execute_cypher(query, {"records": records_with_parent})
        return result[0]["count"] if result else 0

    def finalize(self) -> int:
        """Link every taxon whose parent arrived in a later batch (#273).

        `_load_parent_relationships` resolves the parent with `MATCH`, and runs
        per batch — so a parent that appears later in `nodes.dmp` does not exist
        yet and the row is silently dropped. Nothing retried it, which left
        331,131 taxa (29%) detached, `Faecalibacterium prausnitzii` among them
        (child 853, parent genus 216851 — ~216k records later in the file).

        By the time this runs every node is written, so the linkage can be
        rebuilt from the `parent_taxon_id` now persisted on each node. Driving
        off a stored property rather than the in-memory dump also means this can
        repair an already-loaded graph without re-reading `nodes.dmp`.

        Paged, because the taxonomy is ~1.1M nodes. The `EXISTS` guard matters:
        it restricts each page to rows that WILL link, so a page full of
        unlinkable rows (a parent excluded by `filter_to_microbiome`) cannot
        return zero and end the loop while linkable rows remain.
        """
        query = """
            MATCH (c:Taxon)
            WHERE c.parent_taxon_id IS NOT NULL
              AND NOT (c)-[:HAS_PARENT]->()
              AND EXISTS { MATCH (:Taxon {taxon_id: c.parent_taxon_id}) }
            WITH c LIMIT $limit
            MATCH (p:Taxon {taxon_id: c.parent_taxon_id})
            MERGE (c)-[r:HAS_PARENT]->(p)
            RETURN count(r) AS count
        """
        total = 0
        while True:
            result = self.execute_cypher(query, {"limit": self.batch_size})
            linked = result[0]["count"] if result else 0
            if not linked:
                break
            total += linked
        if total:
            logger.info("Linked %d parent edges missed by batch ordering (#273)", total)
        return total


def download_ncbi_taxonomy(output_dir: str) -> str:
    """
    Download NCBI Taxonomy dump files.

    Args:
        output_dir: Directory to save files

    Returns:
        Path to extracted taxdump directory
    """
    import requests
    import tarfile

    url = "https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump.tar.gz"
    tar_path = os.path.join(output_dir, "taxdump.tar.gz")
    taxdump_dir = os.path.join(output_dir, "taxdump")

    os.makedirs(output_dir, exist_ok=True)

    logger.info(f"Downloading NCBI Taxonomy from {url}")
    # 10s to establish TCP, 300s per chunk read — generous for the ~50MB
    # download but bounded so an NCBI FTP outage doesn't hang the loader
    # indefinitely.
    response = requests.get(url, stream=True, timeout=(10, 300))
    response.raise_for_status()

    with open(tar_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    logger.info(f"Extracting to {taxdump_dir}")
    with tarfile.open(tar_path, "r:gz") as tar:
        # filter='data' (PEP 706, available 3.11.4+) rejects absolute paths,
        # path traversal, and symlinks during extraction — defense-in-depth
        # in case the upstream tarball is ever compromised or replaced.
        tar.extractall(taxdump_dir, filter="data")

    return taxdump_dir


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    from neo4j import GraphDatabase

    load_dotenv()

    NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
    NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

    # Download if needed
    data_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data", "taxonomy")
    taxdump_dir = os.path.join(data_dir, "taxdump")

    if not os.path.exists(taxdump_dir):
        taxdump_dir = download_ncbi_taxonomy(data_dir)

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    try:
        loader = NCBITaxonomyLoader(
            driver=driver,
            organization_id="default",
            taxdump_dir=taxdump_dir,
            filter_to_microbiome=True
        )

        stats = loader.run()
        print(f"Loaded NCBI Taxonomy: {stats.to_dict()}")
    finally:
        driver.close()
