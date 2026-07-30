# Ruleset boundary

## Decision

AI KP Local currently ships exactly one executable ruleset: CoC 7th edition. The code has a
ruleset boundary so a future system does not require copying the platform, but this is not a
claim that arbitrary uploaded rulebooks are executable.

Uploading and indexing a book adds searchable knowledge only. A ruleset becomes executable only
after a reviewed plugin is registered with deterministic mechanics, character validation, source
references, and real-case tests. `GET /rulesets` is the authoritative installed-engine list.
The normative v1 fields and compatibility rules are in
[`RULESET_PLUGIN_SPEC.md`](RULESET_PLUGIN_SPEC.md).

## Dependency direction

```text
API -> application service -> ruleset registry -> installed ruleset
                         \-> repository

ruleset/coc7 -> existing CoC character, spreadsheet, skill, and dice modules
repository   -X-> concrete ruleset implementation
```

Application, API, and storage code must not import `ai_kp.rules.*` directly. They resolve a
campaign's stored `system` through `rulesets/registry.py`. The registry is an explicit local
allow-list; it does not dynamically import code named by an uploaded file or model response.

## Identities

- Campaigns store the stable installed slug, currently `coc7`.
- Campaigns also pin the exact executable ruleset, character schema and event schema versions.
- Persisted mechanical results store the precise implementation/source identity
  `coc7-keeper-cn-2002c` and version `2002c`.
- Rulebook sources independently retain their own content hash and evidence pages.

Aliases are accepted only at campaign creation and normalize to the stable slug. Replays select
the engine from the precise ruleset identity stored on the original check, not from the current UI
selection.

## Current deliberate limitations

The `Ruleset` port exposes only behavior proven by the current CoC vertical slices: character
normalization, the supported Excel import, skill catalogue/recommendation, and replayable skill
checks. The existing check HTTP DTO and SQLite columns remain percentile/CoC-shaped. They should
not be generalized speculatively before a real second ruleset supplies concrete acceptance cases.

The first system-neutral mechanical slice is random evidence. The platform records requested dice
components, physical/digital faces and an integrity fingerprint; CoC7 still interprets shared
percentile digits, bonus/penalty candidates, thresholds and success levels. The legacy `raw_dice`
projection remains compatible while new writes also store `random_evidence`.

The older `rules/` and `characters/` modules remain the CoC implementation internals during this
incremental migration. New application code must enter them through `rulesets/coc7`, while their
old imports remain temporarily stable for existing tests and callers.

## Adding a future ruleset

A second system requires all of the following before registration:

1. A manifest with stable ID, version, aliases, capabilities, and authoritative sources.
2. Character schema, validator, and an explicit migration/import strategy.
3. Pure deterministic check and combat resolvers with stored replay inputs.
4. System-specific AI proposal vocabulary and unresolved-result safety rules.
5. Any required generic check-envelope migration based on that real system's data.
6. API, authorization, restart/replay, frontend, and rulebook-based real-case tests.

Until those conditions pass, an unsupported system fails closed instead of falling back to CoC.

AI behavior is a separate proposal-only boundary. Installed Skill manifests are listed by
`GET /ai-skills`; rulebook extraction may produce source-bound `skill_guidance`, but that guidance
is `reference_only` and is never registered or granted state-write authority automatically.
