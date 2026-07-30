# Scene Director v1

## Purpose

Scene Director bridges imported module knowledge and the live play loop. It does not narrate by
itself and does not grant the model write authority. It records the current scene and the
per-playthrough state of source-linked module entities, then performs a deterministic, read-only
analysis before a later proposal is generated.

The module remains immutable source material. A campaign module run owns all mutable scene and
discovery state, so two campaigns can play the same module without sharing progress.

## Source and runtime separation

| Concern | Source of truth |
| --- | --- |
| Canon, anchors, clues, locations, relations | approved module knowledge and `module_entities` |
| Current module and spoiler scope | `campaign_module_runs` |
| Current scene and play pace | `campaign_module_runs` current projection |
| Scene transition history | `module_run_scene_events` |
| Per-run clue/entity status | `module_run_entity_states` |
| Discovery audit | `module_run_entity_state_events` |
| Narration and world changes | existing proposal approval and event ledger |

Runtime rows reference module entities; they never copy or edit the approved source statement.
Read-time graph filtering still removes entities whose source candidate is no longer current.

## Scene contract

A scene has a stable key, a human title, an optional source-linked location, a world-time
snapshot, and one ruleset-neutral pace:

- `freeform`: conversation, investigation, travel, or other flexible play;
- `structured`: turn-sensitive conflict where individual actions matter;
- `downtime`: hours or days pass and activities are summarized.

These values describe orchestration cadence, not CoC rules. A ruleset adapter decides which
mechanics are valid inside the cadence. Scene transitions are append-only audit events and update
the current projection in the same SQLite transaction.

## Entity state contract

Every current module entity has one status in a playthrough:

- `hidden`: not currently offered by the runtime;
- `available`: reachable or present, but not yet encountered;
- `discovered`: encountered or revealed at the table;
- `resolved`: its immediate runtime purpose has concluded.

The KP may correct a state in either direction, but every change records its previous value,
new value, note, member, and timestamp. The model cannot call this mutation directly.
Audit events are returned in stable insertion order when multiple local changes share one
timestamp, so replay preserves the actual sequence.

## Read-only intent analysis

`POST /module-runs/{run_id}/director/analyze` performs:

1. Load the exact run, current scene, spoiler tags and source hash.
2. Load current provenance-valid module entities and per-run state.
3. Evaluate anchor reachability from available, discovered and resolved entries.
4. Search only sources admitted by the active spoiler scope.
5. If no admitted source matches, search the KP-only complete source only to detect whether a
   future spoiler exists; deferred text is not returned.
6. Return one decision:
   - `needs_scene`
   - `answer_from_canon`
   - `blocked_by_spoiler`
   - `world_gap`
7. Return `writes_performed=false`.

`world_gap` is permission to enter the existing world-expansion proposal boundary, not proof that
the requested place or NPC exists. `blocked_by_spoiler` requires a KP decision and cannot be
silently converted into narration.

The KP UI can turn a current `world_gap` into a source-bound `world_expansion` proposal. The
proposal records the module-run version, source hash, world-fact head hash, deterministic analysis,
model candidate, alternatives and context snapshot. Approval fails closed if any bound runtime
source changed. See `WORLD_EXPANSION.md`.

## Concurrency and authorization

All director endpoints are KP-only and resolve the campaign from the run stored on the server.
Scene transitions and entity-state changes require the latest module-run `expected_version`.
They acquire `BEGIN IMMEDIATE`, recheck the version, append the audit row and update the current
projection atomically. A stale client receives `409 conflict` and must refresh.

The frontend keys drafts to the current run and preserves, but detaches, dirty input if another KP
ends or replaces that run.

## HTTP surface

```http
GET   /module-runs/{run_id}/director-state
POST  /module-runs/{run_id}/scene-transitions
PATCH /module-runs/{run_id}/entities/{entity_id}/state
POST  /module-runs/{run_id}/director/analyze
```

## Real-case acceptance

1. Start a module and transition into a source-linked location.
2. Mark one clue available, then discovered; verify both audit events survive restart.
3. Submit an intent matching a current-spoiler source and receive `answer_from_canon`.
4. Submit an intent matching only a future spoiler and receive `blocked_by_spoiler` without the
   deferred text.
5. Submit an off-route intent with no source and receive `world_gap`.
6. Verify all analyses report zero writes and do not change the run version.
7. Send two mutations with the same version; exactly one succeeds and the other returns `409`.
8. Verify a player token cannot read director state or call any director mutation.

Against a disposable running backend:

```bash
.venv/bin/python scripts/scene_director_realcase.py --base-url http://127.0.0.1:8003
```

When the disposable backend is configured with a reachable LLM, add
`--world-expansion` to exercise generation, review and approval as well.

## Deferred work

Safety pause, private lines/veils, handout reveal, automatic check-consequence return, strict
World Fact materialization after actual contact, and end-of-session memory review are
intentionally separate vertical slices. Their absence keeps this capability `partial`.
