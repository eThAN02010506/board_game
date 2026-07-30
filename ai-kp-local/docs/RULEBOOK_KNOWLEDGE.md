# Rulebook knowledge pipeline

The rulebook subsystem deliberately separates three responsibilities:

1. `rule_chunks` stores immutable, page-addressable source text in SQLite. The same chunks are
   indexed in an isolated MiniRAG working directory for original-source retrieval.
2. `rule_objects` stores versioned JSON candidates. AI output can only become
   `review_required` or `quarantined`; it cannot publish itself.
3. `rulebook.engine` executes a closed declarative DSL. It never evaluates Python, JavaScript,
   formulas, or natural-language conditions.

The extractor may recover more than dice formulas. Source-bound candidates can describe
terminology, ruleset/version metadata, character fields, resources, conditions, action economy,
combat, damage, healing, sanity, chases, advancement, creatures, Keeper guidance, and AI Skill
guidance. Extraction is discovery, not installation: every candidate retains exact page evidence,
and only the closed executable subset can be promoted through deterministic golden cases.
`skill_guidance` is permanently `reference_only`; it may inform a proposal-producing AI Skill but
can never grant that Skill authority to mutate campaign state.

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

Uploaded PDF bytes are never decoded in the API process or its request thread. The request thread
only waits for a fresh `spawn` child that verifies the source SHA-256, applies the existing
`AI_KP_MODULE_PARSE_*` wall/CPU/address-space budgets, and writes a bounded JSON result manifest to
a private temporary directory. Large chunks never cross a multiprocessing pipe. Per-page
decompressed content is limited to 16 MiB and the complete book to 128 MiB; these checks execute
inside the resource-limited child because `pypdf.get_data()` necessarily performs decompression
before its output length is known. Timeout, abnormal exit, malformed manifest, hash mismatch and
cleanup all fail closed before SQLite source creation.

## Storage

- `rule_sources`: source identity, SHA-256, ruleset, page count, and lifecycle.
- `rule_chunks`: page range, chapter, source text hash, extraction and MiniRAG status.
- `rule_ingestion_runs`: resumable stage, page cursor, model and prompt version, counts and error.
- `rule_objects`: versioned candidate JSON, status, confidence and complete validation report.
- `rule_object_citations`: exact evidence text, page and evidence hash.
- `rule_validation_issues`: malformed agent output and failed validation audit records.
- `rule_relations`: explicit cross-rule relations for later graph enrichment.

If the process exits after claiming a chunk but before recording an LLM result, application startup
moves only `processing` chunks back to `pending`. Completed and failed audit states are preserved.
Every claim atomically increments the chunk's durable `attempt_count`. After the external LLM call,
the service takes a short SQLite writer transaction, verifies `processing + attempt_count`, writes
rule objects and validation issues, and completes the chunk in that same transaction. A worker
from before recovery therefore cannot publish results or failure audit records into a newer claim.
Unexpected SQLite begin, write, or commit failures roll back partial results and best-effort move
the still-current attempt to `failed`, so `retry_failed=true` works without another process
restart. Startup also closes leftover `running` ingestion runs as interrupted; an interrupted
MiniRAG index run moves its source out of `indexing`.

Each source and ingestion stage has at most one service-created `running` run. Chunk claims
atomically require both a pending chunk and their owning extraction run to remain `running`, while
run completion is a `running -> completed` CAS. MiniRAG publication uses the same run transition
inside its result transaction. Consequently, neither a stale successful worker nor its later
failure handler can revive a recovered run, claim subsequent chunks, publish stale index IDs, or
overwrite a replacement run's `ready` source with `failed`.

## Validation and human promotion

1. **Schema:** Pydantic validates the closed rule DSL. Unknown executable expressions cannot enter
   the engine.
2. **Source binding:** the candidate `ruleset_id` must equal the immutable ruleset identity of its
   source.
3. **Citation:** every citation must reference a chunk from the same source, use a page inside that
   chunk, and copy evidence present in the stored source after whitespace normalization.
4. **Conflict:** a differing object with the same `rule_key` as an existing validated rule is
   quarantined rather than silently replacing it.

Passing these gates still produces `review_required`, regardless of model confidence. A local
administrator who is also an authenticated KP must review the exact object hash and provide at
least one deterministic golden case. The engine runs every case and compares canonical JSON with
type preservation; only a full pass can promote an executable object to `validated`. Direct
validated inserts are rejected, and legacy rows without bound human-review evidence fail closed in
both search and execution.
`reference_only` material remains available through original-source retrieval rather than being
misrepresented as executable code.

This means a newly uploaded rulebook can gradually supply the facts needed to author a future
ruleset package, while knowledge and execution remain separate. The platform does not claim that
an arbitrary uploaded book is playable: its manifest, schemas, deterministic mechanics, UI
capabilities, and conformance cases must still be reviewed and installed as a versioned ruleset.

Review takes a short SQLite writer transaction and repeats source/conflict validation after it
acquires the lock. The final update compares the original `review_required` status and object hash.
`validated` and `quarantined` are terminal review states, so a delayed approve or reject cannot
overwrite completed human evidence. A failed golden-case approval remains `review_required` and
may be reviewed again with corrected cases.

Raw source text follows a stricter boundary than derived rule objects. Imported Keeper and unknown
PDFs always create `kp` chunks. Historical chunk rows may contain `all` or `player`, but those
values came from extraction and are not reliable evidence of a human publication decision; runtime
therefore treats every raw chunk as KP-only. A player may receive a derived rule object only when
its exact object hash has a passing KP review and the stored object explicitly contains
`audience: all` or `audience: player`. Missing audience fields fail closed for players. This avoids
opening an entire mixed Keeper paragraph merely because one public rule cites a short excerpt.

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

Rule maintenance additionally requires local-administrator access and an authenticated KP:

```http
GET  /rulebooks/sources/{source_id}/rules
POST /rulebooks/rules/{rule_object_id}/review
```

Authenticated session members use:

```http
POST /rules/query
POST /rules/execute
```

Queries report `retrieval_backend` as `minirag` or `lexical_fallback`. KP queries can retrieve raw
page-addressable chunks. Player queries never return raw rulebook chunks; they return only
human-reviewed rule objects with an explicit `all` or `player` audience. Execution applies the
same fail-closed boundary even when a player knows the rule key.

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
`review_required` candidates must be reviewed before gameplay relies on them.
