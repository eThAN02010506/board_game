# Investigator Memory Workspace Stage

Build one campaign-scoped, evidence-backed investigator timeline without
creating a second source of truth.

## Invariants

1. `events` remains the authoritative play history and `memories` remains the
   recall layer. The timeline is a read projection; it never copies either
   record into a new “timeline fact” table.
2. A memory keeps its original text, scope, importance, visibility, and source
   event. KP corrections are immutable `memory_curation_actions` that supersede
   an earlier action; no correction updates or deletes the source memory.
3. Every correction carries the expected current action id. A stale editor
   receives a conflict instead of silently overwriting newer KP judgment.
4. Player access is fail-closed: the session must have an assigned PC, that PC
   must resolve to one approved investigator owned by the authenticated player,
   and the projection may contain only that PC's `table` or `player` memories.
5. KP access is campaign-scoped. KP may filter by investigator, classification,
   visibility, and text, inspect source evidence, and append a correction.
6. Corrections can reclassify (`major`, `side`, `npc`, `clue`, `other`), set
   importance from 1 to 5, hide/restore an item, and add a concise reason.
   Hiding changes only the timeline projection; it does not erase provenance.
7. Player responses expose source-event type, summary, and time only when the
   source event itself is player-visible. KP-only payloads and curation reasons
   never cross the player boundary.
8. The UI uses a finite semantic ordered list of timeline articles. It does not
   claim an ARIA feed or infinite-scroll contract that it does not implement.

## Evidence model

- Primary entity: the immutable memory row.
- Derivation: optional `source_event_id` and a visibility-filtered source-event
  summary.
- Subject: the campaign PC plus its stable investigator id and name.
- Revision: the latest immutable curation action, with the earlier action linked
  through `supersedes_action_id`.

## Real-case acceptance

1. A KP records one sourced major event and one side memory for an approved
   investigator.
2. The player sees both in chronological order and can inspect the permitted
   source summary, but sees no KP-only notes or correction actor.
3. The KP reclassifies the side memory as a clue and raises its importance.
   Refreshing shows the new projection while the source row remains unchanged.
4. Reusing the former action head produces HTTP 409.
5. The KP hides the clue. It remains visible in KP's “include hidden” view and
   disappears from the player projection. A later restore action makes it
   visible again.
6. Another player and another campaign cannot read the investigator timeline.
7. Existing memory search, turn approval, NPC encounter materialization, and
   map behavior continue to pass their regression tests.

## Implementation references

- Microsoft Azure Architecture Center, Event Sourcing: append-only events,
  compensating events, and read projections.
- W3C PROV-O: derivation, primary-source, and revision relationships.
- WAI-ARIA Authoring Practices, Feed Pattern: used only to decide *not* to
  apply the feed role to this finite, non-streaming list.
