# CoC7 Rules Authority and Traceability

## Authoritative Local Source

- Ruleset ID: `coc7-keeper-cn-2002c`
- Title: `克苏鲁的呼唤第七版守秘人规则书`
- Source filename: `call-of-cthulhu-keeper-rulebook-cn-Version2002c.pdf`
- SHA-256: `f6113754ea095de0a60b6fb593f38df0041e0573858c28f41204a1edd039f707`
- Document shape: 400 PDF pages, tagged, unencrypted, no embedded JavaScript.

The user-provided PDF is a local reference, not a repository asset. Do not copy it into Git, ship it in builds, or reproduce its tables and prose in project documentation. A future local rulebook index may store the configured file path, content hash, page-level extraction, and search index under ignored local data, but must never make the source document available through the player API.

Printed book page numbers and physical PDF page numbers are not interchangeable. Every implementation citation uses both when known, for example:

```text
CoC7-KR-CN-2002c §5.2, book p.71, PDF p.72
```

## Rules Authority Order

1. The configured CoC7 core rule is the default mechanical authority.
2. A rule explicitly identified by the book as optional applies only when the campaign enables it.
3. A campaign house rule may override the core rule only when the KP enables and names it; the override and its previous value remain auditable.
4. An ambiguous situation may receive a recorded KP ruling. The ruling is scoped to that campaign unless the KP promotes it to a named house rule.
5. Platform workflow policy, such as character-sheet approval or secret visibility, is labeled `platform_policy` and must not be misrepresented as a rule from the book.

The supported source labels are `coc7_core`, `coc7_optional`, `house_rule`, `keeper_ruling`, and `platform_policy`.

## AI and Deterministic Rule Boundary

The language model may interpret a declared action, identify plausible checks, retrieve relevant passages, and narrate a proposed result. It is not the authority for thresholds, dice arithmetic, damage, healing, sanity loss, growth, movement, chase order, or other state changes.

Those outcomes belong to deterministic ruleset services. Each resolved mechanical decision must retain at least:

- `ruleset_id` and ruleset implementation version;
- one or more section and page references;
- rule-source label and enabled optional/house-rule identifiers;
- normalized inputs, raw dice, bonus or penalty dice, difficulty, and computed outcome;
- the affected character-state version;
- any KP override, reason, actor, and timestamp.

An AI proposal may request a check, but it must not commit facts that depend on an unresolved roll. Only a validated rule result or an explicitly recorded KP ruling can advance the corresponding game state.

## Implementation Index

| Design area | Primary book location | Printed page | PDF page |
| --- | --- | ---: | ---: |
| Investigator creation and derived attributes | Chapter 3 | 23 | 24 |
| Skills, specializations, difficulty and pushed rolls | Chapter 4 | 40 | 44 |
| Checks, opposed checks, bonus/penalty dice and growth | Chapter 5 | 71 | 72 |
| Combat, damage, wounds, healing and firearms | Chapter 6 | 85 | 86 |
| Chase setup, movement and hazards | Chapter 7 | 109 | 112 |
| Sanity checks, insanity and recovery | Chapter 8 | 129 | 130 |
| Mythos tomes and magic use | Chapters 9 and 11 | 143 / 189 | 144 / 190 |
| Keeper rulings, NPCs, information and pacing | Chapter 10 | 153 | 154 |
| Spells | Chapter 12 | 205 | 206 |
| Creatures, deities and beasts | Chapters 13 and 14 | 230 / 238 | 232 / 240 |
| Scenario structure and Keeper-only material | Chapter 15 | 312 | 314 |
| System quick reference | Appendix, Game System Summary | 377 | 378 |

This is a routing index, not a substitute for reading the relevant rule and surrounding exceptions before implementation.

## Delivery Checklist for Game Mechanics

Before a mechanic can be marked available:

1. Record its primary and exception references using the citation format above.
2. Implement calculation and state transition as deterministic, model-independent code.
3. Add examples, boundary cases, invalid-input cases, optional-rule cases, and replay tests.
4. Verify that hidden Keeper information and player-visible explanations are separate.
5. Store enough inputs and outputs to reproduce the decision after restart.
6. Run a KP/player real case, including a rejected or overridden result when the mechanic permits it.
7. Confirm the AI cannot bypass the rules service by writing dependent facts directly into events, memories, or character state.

When the book is silent or ambiguous, stop presenting the behavior as `coc7_core`. Use a recorded `keeper_ruling` or explicit `platform_policy` instead.
