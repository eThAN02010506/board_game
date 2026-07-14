# Architecture

## Runtime Shape

- `src/ai_kp/api`: HTTP API and request schemas.
- `src/ai_kp/core`: settings, SQLite schema, repository methods, IDs.
- `src/ai_kp/modules`: module ingestion, chunk metadata, spoiler boundaries.
- `src/ai_kp/memory`: retrieval and NPC reappearance candidate logic.
- `src/ai_kp/kp`: turn orchestration and prompt assembly.
- `src/ai_kp/llm`: model provider interface and OpenAI-compatible adapter.
- `apps/web`: browser workspace for players and human KP.

## Data Rules

- `events` is the append-only campaign history.
- `memories` is the curated recall layer.
- `campaign_npcs` records how an NPC relates to one campaign.
- Global NPC identity is stored once in `npcs`; this allows cross-module reuse.
- `modules` and `module_chunks` store imported KP material with visibility and spoiler metadata.

## Next Implementation Steps

1. Add a human KP control surface: approve, override, hide, reveal, rewind.
2. Add a map/location graph and route planner.
3. Add structured dice checks and rule-system plugins.
4. Add vector retrieval as an optional local service.
