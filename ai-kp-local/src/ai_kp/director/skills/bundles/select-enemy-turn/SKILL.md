---
name: select-enemy-turn
description: Select one bounded NPC encounter action from a ruleset-owned action and target catalogue without owning dice, effects, or state.
---

# Select Enemy Turn

Choose exactly one candidate action for the active non-player encounter participant.

## Workflow

1. Read only the supplied encounter snapshot, visible conditions, private motivation, allowed actions,
   and legal target IDs.
2. Copy one `action_key` from `allowed_actions`. Copy a `target_id` only when that action requires
   one; otherwise use null.
3. Prefer a tactically plausible choice consistent with the private motivation. If evidence is weak,
   choose defend or end turn.
4. Write `public_intent` as an observable attempt before resolution. Keep the private reason separate.
5. Return a proposal only. The ruleset adapter will validate identifiers, checkpoint dice, resolve
   effects, narrate the committed consequence, and advance the turn.

## Safety Boundaries

- Never invent an action, target, weapon, spell, route, condition, resource, or environmental fact.
- Never roll dice, declare success, calculate damage, mutate state, or end the encounter.
- Never copy `private_motivation`, hidden attributes, secret notes, or unrevealed facts into
  `public_intent`.
- Do not control an investigator unless Session 0 explicitly delegates an idle turn; even then use
  the same closed rules catalogue.
- On ambiguity, make the smallest reversible proposal rather than escalating danger.
