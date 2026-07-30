# Private Random Resolution Stage

Implement a KP-only, event-driven hidden-roll resolver without changing any
ruleset outcome or canonical world fact.

## Invariants

1. Deterministic gates run first: authorization, campaign/NPC relationship,
   lifecycle and era, profession constraints, travel-graph reachability, and
   configured travel limit. Randomness can never bypass a failed gate.
2. A hidden roll happens only after an explicit KP trigger. There is no
   background NPC simulation, per-tick movement, polling, or LLM call.
3. One request produces at most two random values:
   - one `d100` appearance roll;
   - one weighted location selection only when the NPC appears and more than
     one eligible location remains.
4. Use `secrets.randbelow`, injected behind a callable for deterministic tests.
   Never accept a client-supplied seed or raw roll.
5. Resolve all proposed destinations from one multi-source Dijkstra pass.
   Do not run one shortest-path search per destination.
6. Persist an idempotent, immutable KP-private receipt containing the command
   hash, raw roll, threshold, eligible locations, chosen location, actor, and
   trigger. Reusing an idempotency key with different input must fail.
7. The public projection contains only `appears` and the selected public
   location when applicable. Raw rolls, failed candidate locations, reasons,
   and private notes must never be sent through player endpoints, realtime
   session events, or ordinary application logs.
8. A successful appearance decision is not a canonical encounter. It must not
   move a map token, create an NPC, consume a returning-NPC budget, or write a
   World Fact. Existing encounter materialization remains the only fact-writing
   boundary after players actually make contact.

## Apply hidden rolls here

- incidental NPC appearance and selection among already legal locations;
- later: approved ambient encounter tables, optional side-event timing, and
  cosmetic atmosphere variants, using the same private-resolution boundary.

## Do not apply hidden rolls here

- authentication, authorization, visibility, or ownership;
- NPC lifecycle, era, profession, relationship, travel, or budget gates;
- CoC skill-check arithmetic, push rules, damage, sanity, or opposed checks;
- pathfinding, map movement legality, clue availability, scenario anchors;
- approval state, canonical World Facts, NPC history, or module truth;
- content generation that still requires the model to author new material.

## Acceptance

- KP can choose a linked NPC, probability, trigger, and weighted destination
  candidates in the NPC workspace.
- Only legal and reachable destinations enter the roll.
- The API is KP-only on every request and supports idempotent retry.
- A deterministic test RNG proves threshold and weighted-selection boundaries.
- Tests prove one graph traversal handles multiple destinations and prove a
  player cannot create or read private receipts.
- Existing skill-check and world-materialization behavior remains unchanged.
