# Session Recap Review Stage

Generate a bounded, evidence-backed recap draft from one campaign session, then
pause for KP review before creating any durable memory.

## Invariants

1. Closing a session never calls the model and never writes recap memories.
   The KP explicitly generates and reviews a recap near the end of an active
   session, then may close the session separately.
2. The reality-time event window is `session.started_at <= event.created_at <=
   generation_started_at`. Game time continues to come from `event.happened_at`;
   these clocks must not be substituted for each other.
3. Before the model call, freeze the complete bounded event list and compute a
   SHA-256 fingerprint over canonical event content. Refuse oversized windows
   rather than silently dropping early events.
4. A repeated generation request for the same session and event-window
   fingerprint returns the existing run. The model call runs outside a write
   transaction; the short persist transaction rechecks session activity,
   fingerprint, and an existing run.
5. Model output is strict JSON. Each candidate contains one or more source event
   IDs from the frozen window, a valid memory scope, importance, visibility,
   optional campaign PC/NPC IDs, game time, and a KP-only rationale.
6. Unknown IDs, unsourced candidates, duplicate candidates, and invalid campaign
   relationships are rejected. If any supporting event is KP-only, the
   candidate is deterministically tightened to KP visibility.
7. A recap run and its candidates are drafts, not world truth. Generation never
   writes `events`, `memories`, NPCs, maps, facts, or investigator state.
8. Only a KP review command may approve or reject a candidate. Approval accepts
   validated field overrides, creates exactly one memory using the primary
   source event, and records the resulting memory ID in the same transaction.
   Retrying a decided candidate is idempotent only when the decision matches;
   conflicting repeat decisions fail.
9. Player endpoints never expose recap drafts, model rationale, rejected
   candidates, or KP review notes. Approved memories enter the existing
   visibility-aware timeline normally.
10. The UI clearly separates model suggestions from accepted memory and keeps
    session closing as a distinct action.

## Bounded input

- At most 250 events.
- At most 60,000 canonical JSON characters across the frozen event window.
- Event payloads are excluded from the model input; ID, actor, visibility,
  event type, summary, game time, and creation time are sufficient.
- Existing campaign memories are represented by normalized text and source ID
  so the model is instructed not to propose obvious duplicates.

## Real-case acceptance

1. In one active session, create visible and KP-only sourced events for two
   approved investigators.
2. Generate a recap. A repeated request before new events returns the same run.
3. Every candidate cites only frozen-window event IDs; a KP-source candidate is
   never downgraded to player/table visibility.
4. Approving an edited candidate writes exactly one sourced memory. Repeating
   the same approval returns the same memory; attempting rejection afterward
   conflicts.
5. Rejecting another candidate writes no memory.
6. Adding a new event changes the window fingerprint and permits a new run.
7. Players cannot list, generate, or review recap drafts, but can read the
   resulting memory if its approved visibility permits.
8. Closing the session performs no model call and does not silently approve any
   remaining draft.

## Implementation references

- Azure Architecture Center, Event Sourcing: immutable events, projections,
  optimistic concurrency, and idempotent consumers.
- Google Cloud Architecture Center, Human-in-the-loop pattern: pause at an
  explicit review checkpoint before consequential action.
- SQLite partial/unique indexes: enforce one event-window run and efficient
  draft queries without application-only uniqueness.
