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

## Real Case Test

Input:

> 玩家说：我想找在旧码头认识、行业内打过交道的人。

Expected:

- 周怀民 can be proposed if he was previously linked to this PC or campaign.
- The answer should cite that he was a prior报社线人 or码头 contact.
- A totally unrelated NPC should not appear.
- Secret notes must not be shown to the player.
- KP module chunks tagged `secret` must not enter normal context unless explicitly revealed.
