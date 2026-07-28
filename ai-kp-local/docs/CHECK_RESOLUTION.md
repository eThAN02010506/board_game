# Replayable CoC7 checks

## Rule source

The deterministic implementation is based on the uploaded Chinese CoC7 Keeper Rulebook,
source identity `coc7-keeper-cn-2002c`, version `2002c`:

- Chapter 5, printed page 77: critical, extreme, hard, regular, failure and fumble levels;
- Chapter 5, printed page 78: bonus and penalty tens dice with one shared ones die.

Every stored check copies this source reference and ruleset version. The model may request a
check, but it does not calculate the result.

## Code boundaries

- `rulesets/coc7/mechanics/skill_check.py` is the canonical CoC pure resolver.
  `rules/dice.py` remains a compatibility import; neither path has database, HTTP or UI
  dependencies.
- `rulesets/coc7/mechanics/opposed_check.py` compares two selected percentile
  results by success level and then skill/attribute value. An exact tie remains
  an explicit Keeper choice between stalemate and reroll.
- `rulesets/registry.py` is the executable-system allow-list; the installed CoC7
  plugin exposes the pure comparison through `Ruleset.resolve_opposed_check`.
- `infrastructure/database/checks.py` owns check rows, raw dice, audit actions and character target
  lookup.
- `application/check_service.py` owns role checks, digital/physical input orchestration and
  deterministic replay, selecting the engine from the campaign or stored check identity.
- `platform/resolution/check_consequences.py` canonicalizes a terminal check batch. Its AI-safe
  snapshot exposes only effective leaf results, while its deterministic SHA-256 fingerprint
  covers every row in a push chain, including raw dice, overrides, ruleset identity and source
  reference.
- `application/check_consequence_service.py` generates the second-stage draft outside a write
  transaction, then re-reads and fingerprints the check batch before saving it.
- `director/check_consequence.py` owns the strict second-stage output contract. A consequence
  draft cannot request another check or propose a map move.
- `api/routers/checks.py` owns only HTTP DTO transport.
- `api/routers/turns.py` exposes consequence generation and the existing KP proposal review
  endpoints.
- `apps/web/src/features/checks/CheckPanel.tsx` owns the play-page interaction.

Random generation is intentionally outside the pure resolver. Digital rolls use the operating
system CSPRNG; physical rolls submit the visible ones digit and every tens digit. Both paths then
call the same resolver and persist the same `raw_dice` shape.

## State and visibility

The state transition is:

```text
requested -> resolved -> overridden
    |           |
    v           +-> pushed child check (requested)
 cancelled
```

Players can list and resolve only non-hidden checks assigned to their current session member.
Hidden checks never appear in a player list and only KP can resolve them. KP override requires a
reason; the original deterministic result is retained separately. The supplied success level and
`passed` flag must agree with the check difficulty. A pushed roll creates a new linked row and
cannot itself be pushed again; once a parent has a pushed child, its result can no longer be
overridden.

The complete two-stage action flow is:

```text
player action: submitted
  -> check-request proposal: draft -> approved
  -> player action: reviewed
  -> checks: requested -> resolved / overridden / cancelled
                         \-> failed check -> pushed child: requested -> terminal
  -> check.consequence_ready
  -> consequence proposal: draft -> approved
  -> player action: resolved
```

Rejecting a consequence draft leaves the original player action `reviewed`, so the KP can
generate another draft. For a push chain, only the leaf is an effective result for
narration, but the result fingerprint covers the parent and every child. An override or newly
created push therefore makes an older consequence draft stale. Approval rechecks the complete
check ID set and fingerprint inside the proposal savepoint; a mismatch returns a conflict without
applying narration or world effects. After consequence approval resolves the action, later
override or push attempts are rejected.

## Two-stage consequence boundary

An AI or manual proposal containing `proposed_checks` cannot also contain proposed events,
memories, NPC updates or map moves. This prevents a successful clue, damage or relationship
change from entering world state before the roll exists. Approving a safe check-only proposal
creates persistent requested checks.

Once every check linked to the same player action is terminal, the backend emits
`check.consequence_ready`. A KP can then request a consequence proposal. The model receives a
verified, leaf-only snapshot as data; raw dice, source references and override audit payloads
remain covered by the fingerprint rather than being copied into the AI-safe result list. The
draft may propose narration, events, memories and NPC updates supported by the final result. This
first consequence slice cannot request more checks or move map tokens.

The verified batch is a required context source. It is budgeted before optional campaign,
memory, module and map context and cannot be silently dropped; if it cannot fit, generation fails
before calling the model. Check creation, draft generation and final approval each validate that
the checks, reviewed action and approved origin proposal share the same campaign and session.

If any check in the batch is hidden, the entire batch uses the strictest privacy policy. The
snapshot marks the hidden scope, public narration is replaced with a fixed neutral message,
events and memories must be KP-only, and NPC updates are forbidden because they have no
independent visibility field. Both the structured-output parser and the persistence service
enforce this boundary.

Generation is idempotent for the same player action and `result_fingerprint`: retrying returns
the existing live draft or approved proposal without another model call. KP approval revalidates
the approved origin proposal, reviewed action, complete check set and fingerprint before applying
the draft atomically. The service starts the outer write transaction before the repository
savepoint, so proposal effects, concrete checks, action state and realtime outbox either all
commit or all roll back. The action becomes `resolved` only after consequence approval, not merely
when the dice finish.

The pure opposed-check comparator is implemented and replay-tested. It deliberately does not
choose a difficulty level or permit a pushed roll; bonus or penalty dice are selected by the
existing percentile resolver before comparison. The two-stage AI consequence backend is now
implemented. Opposed-check persistence, API orchestration and UI remain future work, so the
capability stays `partial` rather than `available`.

## HTTP surface

```http
POST /campaigns/{campaign_id}/checks
GET  /campaigns/{campaign_id}/checks
GET  /checks/{check_id}
POST /checks/{check_id}/resolve
POST /checks/{check_id}/replay
POST /checks/{check_id}/override
POST /checks/{check_id}/cancel
POST /checks/{check_id}/push
POST /checks/{check_id}/consequence-proposal

GET  /kp/proposals/{proposal_id}
GET  /kp/proposals/{proposal_id}/context
POST /kp/proposals/{proposal_id}/approve
POST /kp/proposals/{proposal_id}/reject
```

`POST /checks/{check_id}/consequence-proposal` is KP-only. The supplied check identifies the
linked player action; the service fingerprints every check for that action rather than treating
the selected row in isolation.

## Real-case acceptance

1. KP creates a hard Spot Hidden check at 60 with one bonus die.
2. The player records ones `4`, tens `4,2`; candidates are `44,24`, selected result is `24`, and
   the check is a hard success.
3. Restart the application and replay the row; the result must match exactly.
4. Create a hidden check and verify it is absent from the player's list and player resolution is
   forbidden.
5. Record a KP override and verify both its reason and the original result survive restart.
6. Attempt to create a proposal containing both an unresolved check and a clue event; the whole
   request must fail without persisting either effect.
7. Resolve every check for a queued action, generate a consequence draft twice, and verify the
   same proposal is returned with one model call.
8. Create a push after a consequence draft, verify the stale draft cannot be approved, resolve
   the pushed leaf, and approve a newly fingerprinted consequence exactly once.
9. Inject a failure while creating concrete checks or writing the realtime outbox and verify a
   new database connection sees no partially approved/rejected proposal or world effect.
10. Try cross-campaign, old-session and mismatched action/proposal links; reject them before any
    model call. Verify a hidden check cannot persist public/table consequences.
