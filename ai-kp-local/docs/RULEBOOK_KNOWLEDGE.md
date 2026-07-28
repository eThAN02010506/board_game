# Rulebook knowledge pipeline

The rulebook subsystem deliberately separates three responsibilities:

1. `rule_chunks` stores immutable, page-addressable source text in SQLite. The same chunks are
   indexed in an isolated MiniRAG working directory for original-source retrieval.
2. `rule_objects` stores versioned JSON candidates. A candidate is executable only when its
   status is `validated`.
3. `rulebook.engine` executes a closed declarative DSL. It never evaluates Python, JavaScript,
   formulas, or natural-language conditions.

Campaign memories, NPC memories, modules, and rulebooks use separate storage and retrieval
namespaces. Deleting or rebuilding a MiniRAG index cannot delete the SQLite source or rule
objects.

## Installation

Core PDF extraction uses `pypdf`. MiniRAG and its local vector dependencies are isolated in an
optional dependency group because the upstream PyPI wheel omits both dependencies and the `kg`
package. The extra is pinned to an upstream Git commit:

```bash
.venv/bin/pip install -e ".[rulebook,dev]"
```

No online embedding API is used. `MiniRagOriginalIndex` supplies a deterministic Chinese-friendly
character n-gram embedding function and writes NanoVectorDB files under
`AI_KP_RULEBOOK_INDEX_ROOT`. The adapter calls MiniRAG's vector storage directly for retrieval so
that `tiktoken` never downloads an OpenAI tokenizer at runtime.

## Storage

- `rule_sources`: source identity, SHA-256, ruleset, page count, and lifecycle.
- `rule_chunks`: page range, chapter, source text hash, extraction and MiniRAG status.
- `rule_ingestion_runs`: resumable stage, page cursor, model and prompt version, counts and error.
- `rule_objects`: versioned candidate JSON, status, confidence and complete validation report.
- `rule_object_citations`: exact evidence text, page and evidence hash.
- `rule_validation_issues`: malformed agent output and failed validation audit records.
- `rule_relations`: explicit cross-rule relations for later graph enrichment.

## Three validation gates

1. **Schema:** Pydantic validates the closed rule DSL. Unknown executable expressions cannot enter
   the engine.
2. **Citation:** every citation must reference a chunk from the same source, use a page inside that
   chunk, and copy evidence present in the stored source after whitespace normalization.
3. **Conflict:** a differing object with the same `rule_key` as an existing validated rule is
   quarantined rather than silently replacing it.

Candidates below `0.75` confidence or with a failed source check become `review_required`.
Conflicts become `quarantined`. Only `validated` objects can be executed. Validation is per
candidate: one truncated or malformed object does not discard valid siblings from the same model
response.

## Runtime safety envelope

The executable schema rejects unknown fields, malformed or excessively deep field paths, mixed
execution shapes, non-finite numbers, and rule objects above the fixed operation budget. Runtime
inputs must be inert JSON-shaped values; validation happens before copying, so custom Python
objects, copy hooks, cyclic or aliased containers, excessive depth, and oversized collections
never enter the executor.

Lookup type errors, invalid arithmetic, and non-finite results are normalized to
`RuleExecutionError`. Successful results are validated again before returning. These limits make
rule execution deterministic and bounded; they do not turn extracted natural language into
executable code.

## API

Rulebook mutation requires local administrator access:

```http
POST /rulebooks/sources
GET  /rulebooks/sources
GET  /rulebooks/sources/{source_id}
POST /rulebooks/sources/{source_id}/index
POST /rulebooks/sources/{source_id}/extract-rules?limit=10&retry_failed=false
```

Upload a PDF as the raw request body and send its URL-encoded name in `X-File-Name`. Extraction is
idempotent by source hash and cannot downgrade a ready source. Failed chunks remain auditable and
are retried only when `retry_failed=true` is explicit.

Session members use:

```http
POST /rules/query
POST /rules/execute
```

Queries report `retrieval_backend` as `minirag` or `lexical_fallback`. Player queries exclude
KP-only chunks and objects; execution applies the same audience boundary even when a player knows
the rule key.

## Real-case acceptance

```bash
.venv/bin/python scripts/rulebook_realcase.py \
  --pdf /absolute/path/to/rulebook.pdf \
  --db /tmp/rulebook-realcase.sqlite3 \
  --index-root /tmp/rulebook-minirag \
  --index \
  --extract-page 101
```

The supplied `Version2002c` PDF produced 400 pages, 388 source chunks, and source SHA-256
`f6113754ea095de0a60b6fb593f38df0041e0573858c28f41204a1edd039f707`. MiniRAG indexed all 388
chunks and retrieved pages 101-103 for a major-wound query. The configured GPT-OSS 20B model
produced two structurally valid candidates whose evidence was not exact; both were correctly kept
in `review_required`. A malformed third candidate was independently rejected. This is a safe
failure, not a validated ruleset: full-book extraction must continue in resumable batches, and the
validated count must be reviewed before gameplay relies on it.
