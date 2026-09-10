# Human KP takeover and override authority

This document describes the existing authoritative interfaces used after a
human KP takes control. It is an implementation map derived from FR-13A, not a
second source of product requirements.

## Control boundary

The active module run owns one versioned director-control fence. Switching to
`safety_paused` or `human_kp` rejects new turn, consequence, world-expansion,
recap, semantic-selection, and narrative model calls; it also cancels tracked
in-process calls. Every writer revalidates the fence before persisting a model
result. Returning to `ai_assist` builds fresh context from committed state and
never resumes an old response.

## Typed override matrix

Human control does not expose a generic JSON patch or raw SQL command. The KP
continues to use the same typed services as ordinary play:

| State | Authoritative interface | Audit/correction model |
|---|---|---|
| Character HP/SAN/conditions/growth | CoC7 gameplay and lifecycle commands | versioned rules event; permanent change still requires player confirmation |
| Dice result | `CheckService.override` | original random evidence retained with KP reason and replacement result |
| NPC identity/relationship/status | NPC workspace and contact/lifecycle services | stable NPC ID plus append-only encounter/lifecycle evidence |
| Weather, quest, hidden plot, world state | typed World Fact ledger | visibility-typed assertion; correction appends a retcon revision |
| Map, route, token, fog | map/revision/route services | expected revision/version and realtime audit event |
| House Rule and safety boundary | Session 0 revision service | new table-contract version and fresh consent where required |
| Module scene/entity/automation | module-run service | expected run version plus scene/entity/control event |
| Narration proposal | proposal approval service | original proposal retained; public override text and actor recorded |
| Inventory/currency/loot | inventory transaction services | command id, expected version, before/after ledger entries |
| Session lifecycle and summaries | continuity service | frozen event window, idempotency id, role-isolated projection |

The matrix is intentionally composed from narrow domain services. A single
"override anything" endpoint would weaken schema validation, player ownership,
visibility, replay, and rollback guarantees.

## Recovery rules

1. Pause or take control before changing state that an in-flight model could
   have read.
2. Use the domain command and provide its required reason/source.
3. Prefer an append-only correction over deletion or in-place history edits.
4. Return control only after all writes commit and the current run version is
   refreshed.
5. AI context is rebuilt from rules, facts, events, characters, NPCs, maps, and
   memories. Spoken but unrecorded GM decisions are not inferred.

## Evidence

- `tests/test_ai_control_gate.py` covers a shared control gate, mid-flight
  cancellation, zero model calls while controlled, and fresh fact context after
  handoff.
- `tests/test_module_runs.py` covers versioned control events and stale-page
  rejection.
- `apps/web/e2e/human-kp-handoff.e2e.ts` covers takeover, blocked AI analysis,
  fact assertion, handback, and the durable audit from the KP UI.
- Domain-specific authorization, rollback, replay, and visibility evidence
  remains with the character, check, fact, NPC, map, inventory, Session 0, and
  continuity tests. This document does not weaken their independent gates.
