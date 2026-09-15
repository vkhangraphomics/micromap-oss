"""API reads must not query relationship types nothing writes (#304).

`/genes/{id}/taxa` matched `(:Gene)<-[:HAS_GENE]-(:Taxon)`. No loader has ever
written `HAS_GENE`, and it is absent from `db.relationshipTypes()` on kgdev, so
the endpoint returned an empty list for every one of the 545 genes -- with a
200, which asserts "no taxa carry this gene" rather than "we cannot answer".
`/pathways/{id}/taxa` carried the same dead branch and so silently lost half its
documented logic.

This is the read-side twin of #298 (phantom `CHILD_OF` in the guardrails). The
guard below is the read-side counterpart of the loader-source check added in
#303: it asks what the code writes and what the routes read, and fails when a
route reads something no writer produces.

The `:Gene` layer is derived from `Protein.gene_name` (#251), so a Gene exists
only where a Protein does, and Protein carries no Taxon edge. There is therefore
no Gene->Taxon path of any length -- this could not be repaired by renaming the
relationship, which is why the endpoint now refuses rather than guesses.
"""
import ast
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_ROUTES = _ROOT / "api" / "routes"

_WRITE = re.compile(
    r"(?:MERGE|CREATE)\s*\([^)]*\)\s*-\s*\[\s*\w*\s*:\s*(\w+)", re.IGNORECASE
)
_READ = re.compile(r"-\s*\[\s*\w*\s*:\s*([A-Z][A-Z0-9_]{2,})\s*[\]*{]")

# Some writers build the type dynamically, e.g. provenance/spine.py does
#   for role, ref in (("SUBJECT", ...), ("OBJECT", ...)):
#       ... f"MERGE (a)-[:{role}]->(n)"
# so the literal type never appears next to MERGE and _WRITE cannot see it.
# For those files only, treat their SCREAMING_CASE string literals as written.
# The trade is deliberate: it can mask a phantom whose name happens to be
# quoted in such a file (a false negative), but it never invents a phantom out
# of a type that really is written (a false positive). A guard that cries wolf
# gets deleted; one that occasionally misses still ratchets.
_DYNAMIC_WRITE = re.compile(r"(?:MERGE|CREATE)\s*\([^)]*\)\s*-\s*\[\s*\w*\s*:\s*\{")
_SCREAMING_LITERAL = re.compile(r"[\"']([A-Z][A-Z0-9_]{2,})[\"']")

# Phantom types still read by routes, each tracked. This is a RATCHET, not an
# excuse list: the guard fails on any type not named here, so it cannot grow
# quietly. Shrinking it is the work; adding to it requires a deliberate edit.
# Emptied by #305: AFFECTS_TAXON was repointed at EFFECTIVE_AGAINST (the only
# Drug<->Taxon edge that exists), and CROSS_FEEDS / PREDICTS / INCLUDES were
# removed with their endpoints, which now return 501 instead of an empty 200.
# Keep this at {} — an entry is a debt, and `test_ratchet_entries_are_still_real`
# fails on any entry that has stopped being a real phantom.
KNOWN_PHANTOM_READS: dict[str, str] = {}


def _query_text(source: str) -> str:
    """Executable string literals only — no docstrings, no comments.

    Scanning raw source would count a docstring that *describes* a removed
    relationship ("this used to match HAS_GENE") as a live query, so the guard
    could never be satisfied by a fix that explains itself. Cypher reaches the
    database as ordinary string constants; docstrings do not.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source

    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))

    return "\n".join(
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and id(n) not in docstrings
    )


def _types_written():
    """Relationship types any non-route module writes."""
    written = set()
    for directory in ("database", "integrations", "micromap-mapforge",
                      "micromap-mcp", "api"):
        root = _ROOT / directory
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if "routes" in path.parts:
                continue
            # Raw source deliberately, unlike the read side: writers use
            # f-strings (f"MERGE (a)-[:{role}]->(n)"), which the AST exposes as
            # JoinedStr rather than a plain constant, so an AST-only pass would
            # miss them and invent phantoms. Over-counting writes is the safe
            # direction — it can hide a phantom, never fabricate one.
            raw = path.read_text(encoding="utf-8", errors="ignore")
            written.update(_WRITE.findall(raw))
            if _DYNAMIC_WRITE.search(raw):
                written.update(_SCREAMING_LITERAL.findall(raw))
    return written


def _types_read_by_routes():
    """Relationship type -> route files that MATCH on it."""
    read: dict[str, set[str]] = {}
    for path in _ROUTES.rglob("*.py"):
        queries = _query_text(path.read_text(encoding="utf-8"))
        for rel_type in set(_READ.findall(queries)):
            read.setdefault(rel_type, set()).add(path.name)
    return read


class TestNoPhantomRelationshipReads:
    def test_routes_do_not_read_types_nothing_writes(self):
        """REGRESSION (#304): the guard that would have caught HAS_GENE."""
        written = _types_written()
        read = _types_read_by_routes()

        phantoms = {t: sorted(f) for t, f in read.items() if t not in written}
        untracked = sorted(set(phantoms) - set(KNOWN_PHANTOM_READS))

        assert not untracked, (
            f"route(s) query relationship types no module writes: "
            f"{ {t: phantoms[t] for t in untracked} }. Such a query returns an "
            f"empty result forever while answering 200, which asserts 'no such "
            f"data exists' rather than 'we cannot answer' (#304). Point it at a "
            f"real edge, refuse explicitly, or add it to KNOWN_PHANTOM_READS "
            f"with a tracking issue."
        )

    def test_has_gene_is_gone(self):
        """The specific phantom this issue is about."""
        read = _types_read_by_routes()

        assert "HAS_GENE" not in read, (
            f"HAS_GENE is still queried by {sorted(read['HAS_GENE'])}; nothing "
            f"writes it, so those queries can only ever return nothing"
        )
        assert "HAS_GENE" not in KNOWN_PHANTOM_READS, (
            "HAS_GENE must be fixed, not ratcheted"
        )

    def test_detector_sees_real_types(self):
        """Proves the guard is not vacuous.

        If `_types_written` returned nothing, every read would look like a
        phantom and the ratchet would be meaningless; if `_types_read_by_routes`
        returned nothing, the guard would pass no matter what. Pin both against
        edges known to exist on kgdev.
        """
        written = _types_written()
        read = _types_read_by_routes()

        for real in ("PRODUCES", "ASSOCIATED_WITH_DISEASE", "HAS_PARENT",
                     "ENCODED_BY"):
            assert real in written, f"detector cannot see writes of {real}"
        for queried in ("PRODUCES", "ASSOCIATED_WITH_DISEASE"):
            assert queried in read, f"detector cannot see route reads of {queried}"

    def test_ratchet_entries_are_still_real(self):
        """A tracked phantom that got fixed must leave the list.

        Otherwise the ratchet rots into a list of names nobody rechecks -- the
        exact failure mode of the label/type lists in #269 and #298.
        """
        written = _types_written()
        read = _types_read_by_routes()

        for rel_type in KNOWN_PHANTOM_READS:
            assert rel_type in read, (
                f"{rel_type} is in KNOWN_PHANTOM_READS but no route reads it "
                f"any more -- remove the entry"
            )
            assert rel_type not in written, (
                f"{rel_type} is in KNOWN_PHANTOM_READS but is now written; "
                f"remove the entry, it is no longer a phantom"
            )


class TestGeneTaxaRefusesInsteadOfLying:
    def test_endpoint_does_not_claim_an_empty_result(self):
        """`/genes/{id}/taxa` must not answer 200 with an empty list.

        There is no Gene->Taxon path of any length: Gene has only ENCODED_BY
        (to Protein), and Protein has no Taxon edge. An empty 200 asserts a
        fact about the biology; the truth is that the linkage is not loaded.
        """
        raw = (_ROUTES / "genes.py").read_text(encoding="utf-8")

        # Executable queries only — the docstring names HAS_GENE on purpose, to
        # explain why the endpoint refuses. Asserting on raw text would forbid
        # documenting the fix.
        assert "HAS_GENE" not in _query_text(raw)
        assert "501" in raw, (
            "the endpoint should refuse explicitly (501) rather than return an "
            "empty list that reads as a biological claim"
        )

    def test_pathway_taxa_no_longer_has_the_dead_gene_branch(self):
        raw = (_ROUTES / "pathways.py").read_text(encoding="utf-8")

        assert "HAS_GENE" not in _query_text(raw)
        assert "PRODUCES" in _query_text(raw), (
            "the metabolite path is the half that actually works and must stay"
        )
