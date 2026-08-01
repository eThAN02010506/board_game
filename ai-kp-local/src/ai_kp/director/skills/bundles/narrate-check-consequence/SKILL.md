---
name: narrate-check-consequence
description: Narrate bounded tabletop RPG consequences from an immutable, server-verified check batch. Use after skill, characteristic, opposed, pushed, hidden, or inherent-risk resolution when prose and proposal candidates must reflect the exact outcome without changing dice, reruling difficulty, or granting unsupported effects.
---

# Narrate Check Consequence

Translate verified rules output into fiction. The snapshot is authoritative and read-only.

## Workflow

1. Read the declared goal, method, target, effect ceiling, roll kind, difficulty, success level,
   opposed result, push state, hidden flag, and any deterministic mechanical effects.
2. Narrate observable cause and consequence at the scale allowed by the effect ceiling. Preserve
   partial success, failure, fumble, or tie semantics exactly.
3. For a pushed roll, incorporate the supplied stakes and deterministic failure consequence. Never
   offer another push or soften a failed push into an ordinary failure.
4. For compound dangerous actions, narrate only the resolved stage. Do not imply that awareness,
   access, movement, injury, escape, or aftermath also succeeded unless their results are supplied.
5. Propose events, memories, NPC updates, or facts only when the verified result directly supports
   them. Keep calculated damage, SAN, resources, and rules state in deterministic fields.
6. For hidden checks, use the required neutral public text and keep result details and consequences
   at KP visibility. Do not leak through tone, numbers, punctuation, or suggested choices.

## Safety Boundaries

- Never alter or recalculate dice, thresholds, success levels, opposed winners, or KP overrides.
- Never request a new check, move map tokens, exceed the predeclared maximum effect, or invent a
  prerequisite that makes the roll retroactively valid.
- Do not turn failure into success without an explicit deterministic fail-forward result.
- Produce consequence candidates only. Application, approval, and further resolution remain
  external.
