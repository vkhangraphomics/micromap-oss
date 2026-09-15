"""
PubMed/MEDLINE Data Loader

Loads biomedical literature data from PubMed via NCBI E-utilities API into Neo4j.
Creates Paper nodes and links them to existing Disease and Taxon nodes.

License: Public Domain (US Government work)

Data source: https://pubmed.ncbi.nlm.nih.gov/
API docs: https://www.ncbi.nlm.nih.gov/books/NBK25501/
"""

from typing import Optional, Iterator, Dict, Any, List
from .base_loader import APIBasedLoader, normalize_disease_name
import xml.etree.ElementTree as ET
import logging
import os
import time

logger = logging.getLogger(__name__)

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


class PubMedLoader(APIBasedLoader):
    """
    Load biomedical literature from PubMed via NCBI E-utilities.

    Creates:
    - Paper nodes (from PubMed articles)
    - MENTIONED_IN relationships (Disease -> Paper, via MeSH terms)
    - MENTIONED_IN relationships (Taxon -> Paper, via abstract text matching)

    Uses E-utilities API:
    - esearch.fcgi to search for PMIDs
    - efetch.fcgi to retrieve article details in XML
    """

    @property
    def source_name(self) -> str:
        return "PubMed"

    def __init__(
        self,
        driver,
        organization_id: str,
        batch_size: int = 100,
        database: str = "neo4j",
        api_key: Optional[str] = None,
        max_papers: int = 10000,
        search_query: str = "microbiome OR microbiota OR gut bacteria"
    ):
        """
        Initialize the PubMed loader.

        Args:
            driver: Neo4j driver
            organization_id: Organization ID for multi-tenant isolation
            batch_size: Records per batch
            database: Neo4j database name
            api_key: NCBI API key (or set NCBI_API_KEY env var)
            max_papers: Maximum number of papers to fetch
            search_query: PubMed search query string
        """
        self.api_key = api_key or os.environ.get("NCBI_API_KEY")
        rate_limit_delay = 0.1 if self.api_key else 0.34

        super().__init__(
            driver,
            organization_id,
            api_base_url=EUTILS_BASE,
            batch_size=batch_size,
            database=database,
            rate_limit_delay=rate_limit_delay
        )
        self.max_papers = max_papers
        self.search_query = search_query

    def _api_params(self, **kwargs) -> Dict[str, Any]:
        """Build API parameters, adding api_key if available."""
        params = dict(kwargs)
        if self.api_key:
            params["api_key"] = self.api_key
        return params

    def extract(self, **kwargs) -> Iterator[Dict[str, Any]]:
        """
        Extract articles from PubMed.

        1. Search for PMIDs via esearch.fcgi
        2. Fetch article details in batches via efetch.fcgi
        3. Parse XML and yield article dicts
        """
        # Step 1: Search for PMIDs
        search_url = f"{self.api_base_url}/esearch.fcgi"
        search_params = self._api_params(
            db="pubmed",
            term=self.search_query,
            retmode="json",
            retmax=self.max_papers,
            sort="relevance"
        )

        logger.info(f"Searching PubMed: {self.search_query}")
        search_data = self.fetch_with_retry(search_url, params=search_params)

        pmids = search_data.get("esearchresult", {}).get("idlist", [])
        if not pmids:
            logger.warning("No PMIDs found for search query")
            return

        logger.info(f"Found {len(pmids)} PMIDs, fetching details...")

        # Step 2: Fetch details in batches of 200
        fetch_url = f"{self.api_base_url}/efetch.fcgi"
        fetch_batch_size = 200

        for i in range(0, len(pmids), fetch_batch_size):
            batch_pmids = pmids[i:i + fetch_batch_size]
            fetch_params = self._api_params(
                db="pubmed",
                id=",".join(batch_pmids),
                retmode="xml"
            )

            time.sleep(self.rate_limit_delay)

            try:
                import requests
                response = requests.get(fetch_url, params=fetch_params, timeout=60)
                response.raise_for_status()
                xml_text = response.text
            except Exception as e:
                logger.exception("Failed to fetch batch starting at %d: %s", i, e)
                self.stats.errors.append(f"Fetch error at offset {i}: {str(e)}")
                continue

            # Step 3: Parse XML
            try:
                root = ET.fromstring(xml_text)
            except ET.ParseError as e:
                logger.exception("XML parse error: %s", e)
                self.stats.errors.append(f"XML parse error: {str(e)}")
                continue

            for article_elem in root.findall(".//PubmedArticle"):
                article = self._parse_article(article_elem)
                if article:
                    yield article

    def _parse_article(self, elem) -> Optional[Dict[str, Any]]:
        """
        Parse a PubmedArticle XML element into a dictionary.

        Args:
            elem: PubmedArticle XML element

        Returns:
            Dictionary with article data, or None if parsing fails
        """
        try:
            # PMID
            pmid_elem = elem.find(".//PMID")
            if pmid_elem is None or not pmid_elem.text:
                return None
            pmid = pmid_elem.text.strip()

            # Title
            title_elem = elem.find(".//ArticleTitle")
            title = title_elem.text.strip() if title_elem is not None and title_elem.text else ""

            # Abstract
            abstract_parts = []
            for abstract_text in elem.findall(".//AbstractText"):
                if abstract_text.text:
                    abstract_parts.append(abstract_text.text.strip())
            abstract = " ".join(abstract_parts)

            # Journal
            journal_elem = elem.find(".//Journal/Title")
            journal = journal_elem.text.strip() if journal_elem is not None and journal_elem.text else ""

            # Year
            year = None
            year_elem = elem.find(".//PubDate/Year")
            if year_elem is not None and year_elem.text:
                try:
                    year = int(year_elem.text.strip())
                except ValueError:
                    pass

            # MeSH terms
            mesh_terms = []
            for mesh_heading in elem.findall(".//MeshHeading"):
                descriptor = mesh_heading.find("DescriptorName")
                if descriptor is not None and descriptor.text:
                    mesh_id = descriptor.get("UI", "")
                    mesh_terms.append({
                        "descriptor": descriptor.text.strip(),
                        "mesh_id": mesh_id
                    })

            # Authors
            authors = []
            for author_elem in elem.findall(".//Author"):
                last_name = author_elem.find("LastName")
                fore_name = author_elem.find("ForeName")
                if last_name is not None and last_name.text:
                    name = last_name.text.strip()
                    if fore_name is not None and fore_name.text:
                        name = f"{fore_name.text.strip()} {name}"
                    affiliation_elem = author_elem.find(".//Affiliation")
                    affiliation = ""
                    if affiliation_elem is not None and affiliation_elem.text:
                        affiliation = affiliation_elem.text.strip()
                    authors.append({
                        "name": name,
                        "affiliation": affiliation
                    })

            return {
                "pmid": pmid,
                "title": title,
                "abstract": abstract,
                "journal": journal,
                "year": year,
                "mesh_terms": mesh_terms,
                "authors": authors
            }

        except Exception as e:
            logger.warning(f"Error parsing article: {e}")
            return None

    def transform(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Transform a parsed article record for loading into Neo4j.

        Args:
            record: Parsed article dictionary from _parse_article

        Returns:
            Transformed record with paper_id and organization_id, or None if no pmid
        """
        if not record.get("pmid"):
            return None

        return {
            "paper_id": f"PMID:{record['pmid']}",
            "pmid": record["pmid"],
            "title": record.get("title", ""),
            "abstract": record.get("abstract", ""),
            "journal": record.get("journal", ""),
            "year": record.get("year"),
            "mesh_terms": record.get("mesh_terms", []),
            "authors": record.get("authors", []),
            "source": "PubMed",
            "organization_id": self.organization_id
        }

    def load_batch(self, batch: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Load a batch of paper records into Neo4j.

        Creates Paper nodes, links to Disease nodes via MeSH terms,
        and links to Taxon nodes via abstract text matching.

        Args:
            batch: List of transformed paper records

        Returns:
            Dictionary with counts of nodes and relationships created
        """
        nodes_created = 0
        relationships_created = 0

        # 1. MERGE Paper nodes
        paper_records = []
        for record in batch:
            paper_records.append({
                "paper_id": record["paper_id"],
                "pmid": record["pmid"],
                "title": record["title"],
                "abstract": record["abstract"],
                "journal": record["journal"],
                "year": record["year"],
                "source": record["source"],
                "organization_id": record["organization_id"]
            })

        if paper_records:
            merge_papers_query = """
                UNWIND $records AS record
                MERGE (p:Paper {paper_id: record.paper_id})
                ON CREATE SET
                    p.pmid = record.pmid,
                    p.title = record.title,
                    p.abstract = record.abstract,
                    p.journal = record.journal,
                    p.year = record.year,
                    p.source = record.source,
                    p.organization_id = record.organization_id,
                    p.created_at = datetime()
                ON MATCH SET
                    p.title = record.title,
                    p.abstract = record.abstract,
                    p.journal = record.journal,
                    p.year = record.year,
                    p.updated_at = datetime()
                RETURN count(p) AS count
            """
            result = self.execute_cypher(merge_papers_query, {"records": paper_records})
            nodes_created += result[0]["count"] if result else 0

        # 2. Link to existing Disease nodes via MeSH terms
        mesh_links = []
        for record in batch:
            for mesh_term in record.get("mesh_terms", []):
                normalized = normalize_disease_name(mesh_term["descriptor"])
                mesh_links.append({
                    "paper_id": record["paper_id"],
                    "name_normalized": normalized,
                    "mesh_id": mesh_term.get("mesh_id", "")
                })

        if mesh_links:
            link_diseases_query = """
                UNWIND $links AS link
                MATCH (d:Disease {name_normalized: link.name_normalized})
                MATCH (p:Paper {paper_id: link.paper_id})
                MERGE (d)-[r:MENTIONED_IN]->(p)
                ON CREATE SET r.via = 'mesh', r.mesh_id = link.mesh_id
                RETURN count(r) AS count
            """
            result = self.execute_cypher(link_diseases_query, {"links": mesh_links})
            relationships_created += result[0]["count"] if result else 0

        # 3. Link to existing Taxon nodes via abstract text matching
        taxon_links = []
        for record in batch:
            if record.get("abstract"):
                taxon_links.append({
                    "paper_id": record["paper_id"],
                    "abstract": record["abstract"]
                })

        if taxon_links:
            link_taxa_query = """
                UNWIND $links AS link
                MATCH (p:Paper {paper_id: link.paper_id})
                MATCH (t:Taxon)
                WHERE link.abstract CONTAINS t.name AND size(t.name) > 3
                MERGE (t)-[r:MENTIONED_IN]->(p)
                ON CREATE SET r.via = 'text_match'
                RETURN count(r) AS count
            """
            result = self.execute_cypher(link_taxa_query, {"links": taxon_links})
            relationships_created += result[0]["count"] if result else 0

        return {
            "nodes_created": nodes_created,
            "relationships_created": relationships_created
        }
