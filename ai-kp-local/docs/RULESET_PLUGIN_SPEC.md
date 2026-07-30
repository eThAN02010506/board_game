# Ruleset Plugin Contract

> Contract version: `1.0.0`
> Current executable implementation: CoC7 only
> Security posture: explicit local allow-list; uploaded books never load code

## Purpose

AI KP Local separates four responsibilities:

```text
platform -> ruleset contract -> installed deterministic ruleset
        \-> proposal-only AI skills
        \-> source-bound module content
```

The platform owns campaigns, seats, authorization, events, facts, memory, NPC identity, maps,
visibility, random evidence, model control, audit and replay. A ruleset owns character legality
and the mechanical interpretation of checks and state transitions. An AI skill may understand,
plan or narrate, but can only return a proposal. A module supplies scenario content and cannot
replace a ruleset.

## Manifest v1

Every installed ruleset exposes:

| Field | Meaning |
|---|---|
| `contract_version` | Public plugin contract version; uses semantic versioning |
| `ruleset_id` | Stable implementation/source identity stored in checks |
| `version` | Exact installed ruleset implementation/source version |
| `slug` and `aliases` | Human-facing campaign creation identifiers |
| `engine_family` | Informational engine family, never dispatch authority |
| `source_version` | Edition or source description |
| `character_schema_version` | Exact character payload contract |
| `event_schema_version` | Exact system event payload contract |
| `knowledge_namespace` | Isolated rules knowledge path |
| `supported_locales` | Locales verified by the plugin |
| `support_level` | `knowledge_only`, `assisted`, `playable_alpha`, or `verified_playable` |
| `source_reference` | Provenance for deterministic mechanics |
| `license` | License ID, content scope and optional attribution |
| `capabilities` | Mechanic features that may be called |
| `map_modes` | Supported spatial abstractions |
| `ui_slots` | Trusted host UI surfaces the plugin can populate |

Manifests reject blank, duplicate or incomplete capability metadata. The registry rejects duplicate
IDs and aliases. `GET /rulesets` lists only explicitly installed executable systems.

The current server deliberately uses an in-process allow-list. Python entry points are a possible
future discovery mechanism for packages already installed and trusted by the local administrator;
they are not a safe way to load model-generated or uploaded content because loading an entry point
executes Python.

## Campaign pinning

Every campaign stores:

- the friendly `system` slug;
- exact `ruleset_id` and `ruleset_version`;
- `character_schema_version`;
- `event_schema_version`.

Every generic ruleset call resolves the stored pin. A missing implementation version or mismatched
schema fails closed. Changing the server's installed plugin does not silently reinterpret an old
campaign. A future upgrade must be an explicit migration that validates the old state and preserves
a rollback snapshot.

## Random evidence versus rules interpretation

`platform/randomness` owns only random facts. A ruleset supplies a `DiceRollRequest`, the platform
generates integer faces using the operating-system CSPRNG, and `DiceRollResult` stores:

- `dice-roll.v1`;
- component keys, counts, sides and minimum values;
- every generated or physically entered face;
- a SHA-256 evidence fingerprint.

The ruleset converts that evidence into its own input and interprets candidates, success levels and
effects. For example, CoC7 requests one shared ones digit and one or more tens digits; the platform
does not know which candidate a bonus die selects. The existing `raw_dice` response remains as a
CoC7 compatibility projection, while `random_evidence` is the system-neutral audit record.

Existing persisted CoC7 checks are backfilled from their recorded percentile faces. Replay verifies
the evidence fingerprint before asking the exact stored ruleset version to interpret it.

## Ruleset interface

The current narrow interface covers behavior proven by CoC7:

- normalize and validate a character sheet;
- import the supported character workbook;
- list and recommend skills;
- validate a requested check;
- describe a generic random request;
- convert digital or physical random evidence into ruleset input;
- resolve and replay a check;
- resolve an opposed check, including audited KP overrides.

Combat, sanity, chase and advancement are already advertised as CoC7 capabilities but remain on
CoC7-specific endpoints during this compatibility stage. They should move behind additional small
optional ports only when a real second system demonstrates the required difference. The project
must not design one speculative universal combat state machine.

## AI Skill contract

`GET /ai-skills` exposes installed proposal-only behavior contracts. Each manifest binds:

- stable skill ID and version;
- category and ruleset scope;
- input and output schema versions;
- allow-listed logical tools;
- required source classes;
- immutable `proposal_only` authority.

The initial registry adapts existing structured workflows without changing their behavior:

- player-action/turn proposal;
- check-consequence narration;
- constrained world expansion;
- session recap and memory curation.

Skills cannot import delivery, storage or concrete ruleset implementations. They do not roll
authoritative dice, validate characters, mutate resources, move tokens, promote facts or decide
visibility. Application services remain the only path from a validated proposal to a transaction.

## Extracting useful information from rulebooks

Rulebook ingestion is not limited to question answering. The evidence extractor can classify
source-bound candidates for:

- terminology and ruleset metadata;
- character fields and resources;
- checks, conditions and action economy;
- combat, damage, healing and system-specific subsystems;
- advancement;
- Keeper/GM guidance and potential AI Skill guidance.

Every candidate retains `source_id`, chunk ID, page, verbatim evidence and content hashes.
Mechanically complete candidates may use the closed deterministic DSL. Interpretive guidance,
including `skill_guidance`, must use `reference_only`; it can be reviewed and retrieved but cannot
be installed as an executable skill from the extraction result.

A future candidate package may assemble these reviewed objects into schemas, rules, guidance and
golden cases. Installation still requires license/source review, schema validation, closed DSL
compilation, deterministic tests, permission/leakage tests and explicit human signing.

## UI policy

The host renders trusted components. A ruleset may select declared UI slots and provide validated
data schemas, but an uploaded book or model response cannot provide executable React.

The Rules page shows installed executable systems separately from uploaded knowledge sources.
Support level is visible; unsupported capabilities must say that human KP adjudication is required.

## Conformance

A compatible ruleset increment must pass:

1. manifest and registry validation;
2. character create/import/invalid-input cases;
3. random evidence and deterministic replay;
4. mechanical golden cases;
5. API authorization and visibility;
6. database migration and restart;
7. AI proposal and human takeover;
8. cross-campaign and cross-ruleset isolation;
9. a scripted real-case scenario.

The first invariant is that the current CoC7 inputs, public projections, results and replay
fingerprints remain compatible while these boundaries are introduced.

## External design references

- [JSON Schema Draft 2020-12](https://json-schema.org/draft/2020-12) defines the declarative
  validation dialect intended for future character and event schemas.
- [Semantic Versioning 2.0.0](https://semver.org/) defines compatibility meaning for the public
  plugin contract, independent from a game's edition label.
- [PyPA plugin discovery](https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/)
  documents entry-point discovery for already installed Python packages.
- [Foundry System Data Models](https://foundryvtt.com/article/system-data-models/) demonstrates
  manifest-declared, system-owned document schemas inside a ruleset-neutral virtual tabletop.
