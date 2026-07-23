# Replayable CoC7 checks

## Rule source

The deterministic implementation is based on the uploaded Chinese CoC7 Keeper Rulebook,
source identity `coc7-keeper-cn-2002c`, version `2002c`:

- Chapter 5, printed page 77: critical, extreme, hard, regular, failure and fumble levels;
- Chapter 5, printed page 78: bonus and penalty tens dice with one shared ones die.

Every stored check copies this source reference and ruleset version. The model may request a
check, but it does not calculate the result.

## Code boundaries

- `rules/dice.py` is the current CoC pure resolver. It has no database, HTTP or UI dependency.
- `rulesets/registry.py` is the executable-system allow-list; `rulesets/coc7` adapts CoC
  mechanics to the application-facing port.
- `storage/repositories/checks.py` owns check rows, raw dice, audit actions and character target
  lookup.
- `application/check_service.py` owns role checks, digital/physical input orchestration and
  deterministic replay, selecting the engine from the campaign or stored check identity.
- `api/routers/checks.py` owns only HTTP DTO transport.
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
reason; the original deterministic result is retained separately. A pushed roll creates a new
linked row and cannot itself be pushed again.

## Unresolved-result boundary

An AI or manual proposal containing `proposed_checks` cannot also contain proposed events,
memories, NPC updates or map moves. This prevents a successful clue, damage or relationship
change from entering world state before the roll exists. Approving a safe check-only proposal
creates persistent requested checks. Opposed checks and the AI follow-up result proposal remain
the next vertical slice, so the capability stays `partial` rather than `available`.

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
```

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
