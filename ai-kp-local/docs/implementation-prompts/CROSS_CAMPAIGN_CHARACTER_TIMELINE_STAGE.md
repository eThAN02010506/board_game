# Cross-Campaign Character Timeline Stage

Provide one stable investigator with an auditable history across campaigns
without carrying temporary runtime damage into a new game or leaking an old
Keeper's secrets.

## Invariants

1. `investigators.id` remains the stable global identity. Campaign PCs,
   approved card revisions, runtime state and session participation are
   projections scoped to one campaign or session.
2. The primary timeline branch may have at most one active participation.
   A player who intentionally wants a parallel continuity must create and
   select another named branch; the platform never silently merges branches.
3. Session close completes that session's active participations. Repeated close
   or retry operations do not create duplicate participation rows.
4. Cross-campaign history is a read projection over sourced memories,
   participation records, NPC encounters and accepted permanent changes. It is
   not another writable fact store.
5. A player may read only timelines for investigators owned by their stable
   player profile. A Keeper may read the selected investigator only when it is
   approved in the Keeper's current campaign.
6. Old-campaign `kp`/`secret` memories, hidden memories, source summaries and
   review rationale never enter an inherited Keeper or player projection. A
   current-campaign Keeper may retain the existing campaign-scoped KP view.
7. A permanent change starts as a sourced Keeper proposal. The source event
   must belong to the same campaign and the investigator must be approved
   there. Proposal creation changes no card revision.
8. Only the owning stable player profile can accept or reject a permanent
   change. Acceptance requires the investigator's current revision to equal
   the proposal's frozen base revision.
9. Acceptance deterministically applies a bounded supported change, re-runs
   the ruleset character validator, creates one immutable milestone revision,
   and records the resulting revision in the same transaction.
10. Repeating the same decision is idempotent. A different second decision or
    accepting after another card revision was created returns a conflict.
11. Current HP, SAN, MP, Luck, conditions, inventory delta and other campaign
    runtime state are never copied into a new campaign. The new campaign state
    initializes from the separately approved immutable revision.
12. Supported initial permanent changes are:
    - `major_experience`: append sourced text to traits;
    - `scar`: append sourced text to injuries/scars;
    - `relationship`: append sourced text to significant people;
    - `spell`: append sourced text to the private character secret;
    - `characteristic`: replace one named characteristic with a bounded value;
    - `skill`: replace one existing skill's current value by changing only its
      development points.

## Real-case acceptance

1. An investigator enters campaign A on the primary branch, receives temporary
   HP damage, discovers a visible clue and has an old-campaign secret memory.
2. While campaign A is active, approving the same primary branch in campaign B
   conflicts. Creating and explicitly selecting a parallel branch permits it.
3. Campaign A's Keeper proposes a sourced scar. Before player acceptance, the
   investigator's current revision is unchanged.
4. The owning player accepts the proposal and receives exactly one new
   immutable milestone revision. Identical retry returns it; conflicting retry
   fails.
5. After campaign A closes, its primary participation is completed. The
   investigator can enter a later campaign on the primary branch.
6. The later campaign starts with full derived HP rather than campaign A's
   temporary HP, while the accepted scar remains in the approved card version.
7. Player and later Keeper timelines include the visible clue, participation
   and accepted scar, but exclude the old Keeper secret and review rationale.
8. A different player profile and an unrelated Keeper cannot read or decide
   the timeline.

## Implementation references

- Azure Architecture Center, Event Sourcing: immutable intent events,
  optimistic concurrency, idempotent consumers and rebuildable projections.
- Martin Fowler, Event Sourcing: temporal queries are derived from the event
  history rather than maintained as a second mutable truth.
- SQLite partial unique indexes and immediate transactions: enforce one active
  participation per branch and serialize revision acceptance.
