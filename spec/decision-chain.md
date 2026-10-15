# Decision chain format

Version 0.2

A decision log is append-only. Its export is a JSONL stream in which each entry commits to the
entry before it through a SHA-256 hash chain. Anyone holding an export can recompute the chain
and its **head** (the last digest) without access to the system that produced it. If they record
the head and later compare it with a fresh export, any edited, deleted, reordered or truncated
entry shows up as a mismatch.

## Entry

Each line of the export is one JSON object:

```json
{"seq": 1, "prev": null, "digest": "sha256:<64 hex>", "event": { ... }}
```

| Field | Meaning |
|---|---|
| `seq` | 1-based position in the chain |
| `prev` | The previous entry's `digest`, or `null` for the first entry |
| `digest` | `sha256:` followed by the lowercase hex digest defined below |
| `event` | The decision event, exactly as exported |

## Digest

```
canonical = JSON of event with keys sorted, separators "," and ":", no ASCII escaping, UTF-8
material  = (prev or "") + "\n" + canonical
digest    = "sha256:" + hex(sha256(material))
```

In Python:

```python
canonical = json.dumps(event, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
digest = "sha256:" + hashlib.sha256(((prev or "") + "\n" + canonical).encode("utf-8")).hexdigest()
```

## Verifying

1. Walk the entries in order, keeping the running head (initially `null`).
2. Each entry's `prev` must equal the running head. If not, an entry was deleted or reordered.
3. Recompute the digest from `prev` and `event`. It must equal `digest`. If not, the event was
   modified.
4. The final digest is the head. Compare it with the head you recorded earlier. A chain whose
   tail was cut off still checks out internally, so only this comparison catches truncation.

The reference implementation is `micromap_format/chain.py`. To check an export from the command
line:

```
python -m micromap_format.verify_chain export.jsonl --expect-head sha256:...
```

It exits 0 if the chain is intact and matches the expected head, and 1 otherwise.
