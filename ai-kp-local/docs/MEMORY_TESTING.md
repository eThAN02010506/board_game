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

## Real Case Test

Input:

> 玩家说：我想找在旧码头认识、行业内打过交道的人。

Expected:

- 周怀民 can be proposed if he was previously linked to this PC or campaign.
- The answer should cite that he was a prior报社线人 or码头 contact.
- A totally unrelated NPC should not appear.
- Secret notes must not be shown to the player.
- KP module chunks tagged `secret` must not enter normal context unless explicitly revealed.

Run the focused deterministic suite with:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  .venv/bin/python -m unittest tests.test_memory tests.test_context_builder
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
