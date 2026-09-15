"""#325: standalone verifier for an exported decision-log chain.

Run it over the JSONL a counterparty received (or exported themselves):

    python -m api.provenance.verify_chain <file.jsonl> [--expect-head sha256:...]

It replays the chain, recomputes every digest, prints the head, and exits non-zero
on any break — or, with ``--expect-head``, if the recomputed head does not match the
head you recorded earlier (the actual tamper check). Pure: no database, no network,
so a counterparty can run it — or reimplement it from `chain.py`'s ~10-line digest —
and get the same answer.
"""
from __future__ import annotations

import json
import sys

from .chain import verify_chain


def _load_jsonl(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: python -m api.provenance.verify_chain <file.jsonl> "
              "[--expect-head sha256:...]", file=sys.stderr)
        return 2
    path = argv[0]
    expect = argv[argv.index("--expect-head") + 1] if "--expect-head" in argv else None

    result = verify_chain(_load_jsonl(path))
    print(f"entries: {result.count}")
    print(f"head:    {result.head}")
    if not result.ok:
        print(f"FAIL:    {result.reason}", file=sys.stderr)
        return 1
    if expect is not None and result.head != expect:
        print(f"FAIL:    head mismatch — expected {expect}, got {result.head} "
              f"(the log was truncated or altered since you recorded that head)",
              file=sys.stderr)
        return 1
    tail = " and matches the expected head" if expect else ""
    print(f"OK:      chain is internally consistent{tail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
