# Implementation Prompt — NPC Workspace and Travel Graph

Use this document as the binding implementation and review checklist for the
next local-first AI KP vertical slice.

## Outcome

A human KP can manage NPC appearance metadata and campaign policy from a real
NPC page. Location plausibility is no longer a flat string comparison when the
campaign has an explicit travel graph: the server returns an explainable
shortest route and travel duration and enforces it again at materialization.

## Required design

1. Add a campaign-scoped travel graph:
   - canonical location nodes with bounded, normalized aliases;
   - undirected or directed routes;
   - positive integer travel duration in minutes;
   - travel mode, open/blocked state, KP notes and source provenance;
   - uniqueness and foreign-key constraints in migration v29.
2. Add `max_travel_minutes` to the campaign NPC reappearance policy. Keep zero
   meaningful: only the same canonical location is reachable.
3. Implement deterministic weighted shortest-path search using non-negative
   travel minutes and a priority queue. Return the ordered node/edge path,
   total minutes and a machine-readable outcome. Do not use map pixel distance
   or invent missing travel times.
4. Resolve NPC availability `location_tags` and requested context location
   through canonical node names and aliases:
   - direct alias match costs zero;
   - an open path within the campaign limit is allowed and explained;
   - a known path over the limit or a known disconnected graph is blocked when
     location matching is strict;
   - unresolved names remain `needs_review` during discovery and fail closed
     during final strict materialization.
5. Preserve all existing relationship, lifecycle, era, profession and budget
   checks. Re-run the complete gate after `BEGIN IMMEDIATE`.
6. Add KP-only application services and strict HTTP contracts for listing and
   editing:
   - campaign NPCs with availability profiles;
   - reappearance policy;
   - travel locations and routes;
   - route preview.
   Transport routers must not perform repository writes directly.
7. Replace the `/npcs` placeholder with a dedicated responsive NPC workspace:
   - NPC selector and public/campaign summary;
   - availability profile editor;
   - campaign policy editor;
   - travel-node and route editor;
   - shortest-route preview;
   - clear empty, loading, permission and validation states.
8. Never expose `secret_notes`, `kp_notes`, foreign campaign history or travel
   graph data to players. The whole workspace is KP-only.
9. Keep current COC behavior but use system-neutral persistence names.

## Compatibility and restraint

- Existing campaigns and NPCs migrate without fabricated graph nodes.
- Existing flat location tags remain supported as a fallback.
- Travel routes are explicit KP facts; AI suggestions never auto-persist.
- Do not add NetworkX or a GIS dependency for this bounded graph.
- Do not silently convert legacy free-text `map_routes.travel_time`.
- Do not commit unless the user explicitly requests it.

## Verification

- Add migration/base-schema parity and repository/application/API tests.
- Test directed routes, blocked routes, shortest weighted choice, no path,
  alias resolution, strict final enforcement, rollback and authorization.
- Test the NPC workspace UI and API client.
- Run backend/frontend full suites, TypeScript, production build, Ruff,
  database integrity, foreign-key checks and `git diff --check` twice.
- Extend the isolated real HTTP scenario against the configured 8001 model so
  a returning NPC passes through a multi-hop route within the time limit, while
  a deterministic over-limit preview is rejected.
