# Architecture skeleton

This skeleton records the intended migration destinations without claiming unfinished product
capabilities. Placeholder Python files contain only a module docstring; placeholder TypeScript
files contain only comments and export nothing. They must not be mounted as API routes, imported
into the running application, or marked available in the capability catalogue before a real
vertical slice and its acceptance tests exist.

## Current implementation versus migration targets

| Current working location | Target location | State |
|---|---|---|
| `rules/dice.py` | `rulesets/coc7/mechanics/skill_check.py` | migrated; old path is a compatibility import |
| `rules/coc7_character.py` | `rulesets/coc7/character/validator.py` | migrated; old path is a compatibility import |
| `rules/coc7_skills.py` | `rulesets/coc7/character/skills.py` | migrated; old path is a compatibility import |
| `rules/coc7_recommendations.py` | `rulesets/coc7/character/recommendations.py` | migrated; old path is a compatibility import |
| `characters/xlsx_import.py` | `rulesets/coc7/character/xlsx_import.py` | migrated; old path is a compatibility import |
| `kp/` | `director/` plus `infrastructure/database/context_assemblies.py` | migrated; old paths are compatibility imports |
| `memory/` | `platform/memory/` | migrated; old paths are compatibility imports |
| `modules/` | `platform/modules/` | migrated; old path is a compatibility import |
| `maps/` | `platform/scenes/` plus `infrastructure/database/maps.py` | migrated; old paths are compatibility imports |
| `rulebook/` | `rule_authoring/` plus `infrastructure/knowledge/` | migrated; old paths are compatibility imports |
| `llm/` | `infrastructure/llm/` | migrated; old paths are compatibility imports |
| `security/` and `realtime/` | `infrastructure/security/`, `infrastructure/realtime/`, and database adapters | migrated; old paths are compatibility imports |
| `storage/`, `core/db.py`, `core/repository.py` | `infrastructure/database/` | migrated, including schema migrations; old paths are compatibility imports |
| `core/config.py` and `api/app.py` | `bootstrap/settings.py` and `bootstrap/composition.py` | migrated; stable ASGI and old paths remain compatibility imports |
| root `App.tsx` and realtime hook | `app/App.tsx` and `realtime/provider.tsx` | migrated; old paths are compatibility exports |
| remaining large UI feature extraction | `features/play/`, `ruleset-ui/`, and feature pages | scaffolded; migrate only with behavior tests |

## Package intentions

- `platform/` contains system-neutral facts, actors, sessions, scenes, modules, memory, knowledge,
  and multi-stage resolution concepts. It must not import a concrete ruleset.
- `rulesets/sdk/` will replace the initial broad protocol with small optional capability ports.
- `rulesets/coc7/` is the canonical home of verified CoC7 behavior.
- `director/` will split AI KP planning into scoped stages. It may create proposals but cannot
  commit authoritative state.
- `rule_authoring/` will convert isolated rulebook evidence into untrusted declarative drafts,
  tests, and review artifacts.
- `infrastructure/` is the canonical home of SQLite, MiniRAG, model, realtime, and security
  adapters. Audio and sandbox adapters remain inactive scaffolding.
- `bootstrap/` owns runtime settings, dependency composition, and lifecycle only.
- `ruleset-ui/` will render approved declarative schemas; generated arbitrary React code is not
  part of the design.

## Migration rule

Each migrated destination is now canonical. Old Python and TypeScript paths are intentionally thin
compatibility exports, not a second implementation. New code must import canonical packages.
Remove a compatibility path only in a deliberate breaking release after external callers migrate.

Do not create runtime data directories merely to mirror the design. Knowledge, authoring, map,
audio, backup, and ruleset-build directories are created by safe storage adapters only when real
data exists, using server-generated identifiers and content hashes.
