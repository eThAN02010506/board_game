# CoC7 gameplay state machine

This document describes the authoritative, replayable CoC7 runtime added in
migration 42. It is intentionally separate from AI narration: the model may
propose what happens, while deterministic code and the human KP commit rules
state.

## Authority

The implementation is traced to the user-provided Chinese Keeper Rulebook
(`coc7-keeper-cn-2002c`) and cross-checked against Chaosium's public rules:

- [Combat](https://callofcthulhuwiki.chaosium.com/rules/combat.html)
- [Hit Points, Wounds, and Healing](https://callofcthulhuwiki.chaosium.com/rules/hit-points-wounds-and-healing.html)
- [Sanity](https://callofcthulhuwiki.chaosium.com/rules/sanity.html)
- [Rewards of Success](https://callofcthulhuwiki.chaosium.com/rules/rewards-of-success.html)

Every persisted transition records the ruleset ID/version, source reference,
input payload, generated or physical dice, result, aggregate version, actor,
visibility, and an idempotency command ID.

## Aggregates

`coc7_encounters` stores combat or chase aggregates. Its JSON state contains
participants, ordered turns, reactions, chase locations, action points, HP and
conditions. NPC HP and conditions are removed from player projections.

`investigator_campaign_state` remains the campaign-scoped character aggregate.
It stores HP, SAN, MP, luck, conditions, daily SAN totals and the current game
time. Permanent skill changes still go through the character timeline proposal
and player confirmation workflow.

`coc7_gameplay_events` is the append-only transition log for both aggregates.
`(session_id, command_id)` is unique, so a network retry returns the original
transition instead of applying damage or recovery twice. Aggregate versions
provide optimistic concurrency.

## Combat

- initiative is descending DEX; a readied firearm contributes `DEX + 50`;
- only the active participant may attack, fire, or perform a maneuver;
- unconscious, dying, and dead participants cannot act;
- attack-versus-dodge and attack-versus-fight-back use CoC7's asymmetric tie
  rules;
- fighting maneuvers enforce Build difference and the maximum two penalty dice;
- defense reactions are counted per round and subsequent melee attacks require
  the outnumbered bonus die;
- firearm and melee results are non-pushable;
- damage is recorded from a direct value or a bounded dice expression;
- single-blow major wounds, massive-damage death, unconsciousness, dying and
  round-by-round CON decisions are persisted;
- direct encounter damage to a linked investigator atomically updates the
  campaign character state with a derived idempotency key.

Weapon-specific range, multiple-shot, cover and automatic-fire modifiers remain
explicit KP/check inputs. They do not let the model write HP directly.

## Healing

- First Aid restores one HP or stabilizes dying and can succeed once per injury
  episode;
- Medicine restores recorded `1D3`-style healing and can succeed once per
  injury episode;
- new damage opens a new treatment episode;
- an investigator without a major wound naturally heals one HP per game day;
- a major wound uses a weekly CON result, `1D3` on success or `2D3` on an
  Extreme/Critical result;
- a major wound clears on an Extreme/Critical result or at half maximum HP;
- a calendar `period_id` prevents the same natural-healing period being applied
  twice.

## Sanity

SAN transitions record success/failure loss dice, the SAN roll, any required
INT roll, bout table result and duration. The aggregate tracks:

- temporary insanity after a single loss of five or more and a successful INT
  roll;
- indefinite insanity after losing at least one fifth of the day's starting
  SAN;
- permanent insanity at zero SAN;
- active bouts of madness and explicit bout completion;
- explicit reset of the daily SAN baseline using game-world time.

## Chase

A chase has an ordered location track, pursuer/fleeing roles, CON-adjusted MOV,
round action points, turn order, movement direction and replayable hazards.
Failed hazards can consume extra action points and apply damage through the
same health rules.

## Development

A successful ordinary skill check automatically adds a runtime growth mark.
Positive bonus-die checks do not. Opposed checks mark only the successful
winner after the contest is resolved. Characteristics, Credit Rating and
Cthulhu Mythos are not eligible.

Marks keep their source check IDs, so a KP override can remove only the source
it invalidated. Development rolls are allowed only for marked skills, clear the
consumed runtime mark, and create a permanent-change proposal whether or not
the skill improves. A player must accept that proposal before a new canonical
investigator revision is created.

## API and permissions

- `POST /campaigns/{campaign_id}/coc7/encounters` — KP only
- `GET /campaigns/{campaign_id}/coc7/encounters` — campaign members
- `GET /coc7/encounters/{encounter_id}` — same campaign and session
- `GET /coc7/encounters/{encounter_id}/events` — same campaign and session
- `POST /coc7/encounters/{encounter_id}/commands` — KP only
- `GET /campaigns/{campaign_id}/investigators/{investigator_id}/coc7/state`
  — KP or the investigator's controlling player
- `POST /campaigns/{campaign_id}/investigators/{investigator_id}/coc7/commands`
  — KP only

The automated authorization matrix covers player mutation denial and
cross-campaign/cross-session isolation. Pure mechanics, API persistence,
idempotent retry, optimistic versions, private NPC projection and UI role
projection have dedicated regressions.
