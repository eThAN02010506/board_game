# Memory Testing

Use a fixed fixture campaign such as `雾港 1928` to test memory behavior after every major change.

## Tests To Keep

1. Storage correctness: event and memory rows are written with source IDs.
2. Perspective correctness: player queries do not return KP-only secrets.
3. Retrieval correctness: relevant PC/NPC memories rank above unrelated memories.
4. Context assembly correctness: KP turns include only permitted memories.
5. Consistency: restarting the app does not change factual recall.
6. Model switch: changing the LLM does not change stored facts.
7. False recall: the AI should say it has no record when a fact was never stored.
8. Module boundary correctness: player views cannot read KP-only or secret chunks.
9. Scope isolation: a player query can include that PC's memories and table-shared memories, but
   never another PC's private memory or a different campaign's memory.
10. Empty boundaries: an empty query, empty visibility set, or non-positive limit returns no
    results without constructing invalid SQL.
11. Stable replay: equal-score memories and NPC candidates keep the same order across repeated
    reads.
12. NPC matching normalization: location and profession hints ignore surrounding whitespace and
    case, while Chinese action text can overlap Chinese notes without relying on spaces.
13. Timeline provenance: a visible memory retains its source event ID, while a KP-only source
    summary is redacted rather than causing the whole memory to disappear.
14. Append-only curation: reclassification, importance, hide and restore create immutable actions;
    the original memory row never changes.
15. Concurrent curation: an editor with a stale `expected_head_action_id` receives HTTP 409.
16. Timeline ownership: players require an assigned, approved, player-owned investigator and can
    never select another PC or include hidden items.
17. Recap draft boundary: generating a session recap creates only evidence-bound candidates and
    never writes a memory before explicit KP approval.
18. Recap evidence boundary: every candidate cites one or more events from the frozen session
    window; unknown IDs, cross-campaign PCs/NPCs and unsafe visibility escalation are rejected.
19. Recap idempotency: the same frozen event-window hash reuses one run, repeated identical review
    returns the same memory, and a conflicting second decision returns HTTP 409.
20. Recap lifecycle: rejecting a candidate creates no memory, approving creates exactly one sourced
    memory, and closing a session neither calls the model nor auto-approves drafts.

## Real Case Test

Input:

> 玩家说：我想找在旧码头认识、行业内打过交道的人。

Expected:

- 周怀民 can be proposed if he was previously linked to this PC or campaign.
- The answer should cite that he was a prior报社线人 or码头 contact.
- A totally unrelated NPC should not appear.
- Secret notes must not be shown to the player.
- KP module chunks tagged `secret` must not enter normal context unless explicitly revealed.
- The player timeline shows only that approved investigator's visible memories.
- KP can reclassify one item as a clue, hide it, and restore it; the original memory and source
  event remain byte-for-byte unchanged.
- KP can generate a bounded post-session recap draft, edit a candidate, approve one item, reject
  another, and see only the approved item appear on the permitted timeline.

Run the focused deterministic suite with:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  .venv/bin/pytest -q tests/test_memory.py tests/test_context_builder.py \
  tests/test_session_permissions.py tests/test_session_recaps.py
```
# Automated retrieval benchmark

The deterministic benchmark accepts a JSON array anchored to durable memory
IDs. Each case declares the query, campaign/PC visibility scope, expected IDs,
forbidden IDs and `top_k`. Run it against a copied test database:

```bash
PYTHONPATH=src .venv/bin/python scripts/evaluate_memory_retrieval.py \
  data/evaluation/memory-cases.json \
  --db data/evaluation/ai-kp-copy.sqlite3
```

The report includes Recall@k, reciprocal rank, and forbidden-result hits.
Any forbidden hit returns a non-zero process status. Keep KP-secret IDs and
cross-campaign IDs in `forbidden_ids` so retriever changes cannot silently
weaken spoiler isolation.
