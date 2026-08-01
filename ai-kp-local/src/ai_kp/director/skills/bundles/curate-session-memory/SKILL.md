---
name: curate-session-memory
description: Extract durable, source-linked tabletop RPG memory candidates from a frozen session event window. Use for post-session recap generation that must distinguish major events, side progress, clues, NPC interactions, relationships, and location facts while preserving visibility and avoiding duplicate or invented memories.
---

# Curate Session Memory

Compress the supplied event window into useful retrieval candidates, not a literary session
summary. Every candidate must remain auditable back to immutable events.

## Workflow

1. Treat the frozen event window, allowed character and NPC IDs, existing memories, and visibility
   labels as the complete evidence boundary.
2. Group events that describe the same outcome before selecting candidates. Preserve the smallest
   set of source event IDs sufficient to support each statement.
3. Prefer durable state: major outcomes, unresolved side progress, actionable clues, meaningful NPC
   encounters or relationship changes, and location facts likely to matter later.
4. Exclude greetings, repeated narration, dice mechanics without lasting effect, administrative
   operations, and facts already represented by an existing memory.
5. Write each candidate as a concise, objective statement that can stand alone in later retrieval.
   Keep rationale separate and KP-visible.
6. Set visibility to the strictest level among supporting events. Use player visibility only with a
   valid PC ID; never reveal one investigator's private knowledge to another.
7. Return no candidate when evidence is ambiguous, IDs are unavailable, or nothing is durable.

## Safety Boundaries

- Never invent, correct, merge, or reorder events to make a cleaner story.
- Never cite an ID outside the supplied event window or infer hidden facts from player-visible
  outcomes.
- Never write memory state directly or bypass human/automatic review policy.
- Produce deduplicated memory candidates only; deterministic services validate sources, visibility,
  identity, and persistence.
