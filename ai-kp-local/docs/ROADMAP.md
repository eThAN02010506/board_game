# Capability Roadmap Protocol

This file explains how the roadmap is maintained; it is not a second roadmap snapshot.

## Authoritative Sources

- [`PRODUCT_REQUIREMENTS.md`](PRODUCT_REQUIREMENTS.md) is the **sole normative product source** for
  user needs, scope, functional/non-functional requirements, Real Player Standard and release acceptance.
  No roadmap, README, capability entry or design document may add or relax product semantics.
- `src/ai_kp/planning/capabilities.py` is the only source of capability IDs, labels, current delivery
  status, phase, audience, dependencies and concise implementation summaries. Its acceptance entries are
  delivery checks derived from PRD IDs, not a second source of product requirements.
- [`PRD_IMPLEMENTATION_AUDIT.md`](PRD_IMPLEMENTATION_AUDIT.md) binds requirements to code, API, UI,
  tests and real-case evidence at an explicit commit. It is an audit snapshot, not a status source.
- `GET /capabilities` is the machine-readable view used by clients. Use `GET /capabilities?include_available=false` to inspect only work that is not fully delivered.
- The browser's "功能规划" view reads that endpoint at runtime. It must not keep its own status constants or pretend a planned endpoint exists.
- [`references/LONG_TERM_TRPG_APP_PROMPT_2026-08-21.txt`](references/LONG_TERM_TRPG_APP_PROMPT_2026-08-21.txt)
  preserves user-provided source material. It is evidence of intent; integrated requirements live only in the PRD.

The catalogue covers both current MVP foundations and the complete planned surface. That includes the newer tracks for per-seat invitations, multi-format module documents, ruleset plugins, local semantic memory search, map reveal editing, and campaign backup/restore. Their current status and dependency graph must be read from the catalogue rather than copied here.

## Status Meaning

- `planned`: the acceptance boundary is defined, but users cannot complete the end-to-end workflow.
- `partial`: a tested foundation exists, but at least one acceptance requirement or user-facing step is missing.
- `available`: every linked PRD requirement and catalogue delivery check passes through the supported
  API and UI workflow with current evidence. A backend service, unit test or one-off real-case alone is insufficient.

Source files or placeholder UI alone do not justify `partial` or `available`. A partial implementation must describe exactly what works and what remains in the catalogue summary.

## Current Check-Resolution Boundary

The `check_resolution` capability remains `partial`, but the result-driven second stage is no
longer a missing backend item. Terminal checks linked to one player action are normalized into a
leaf-only AI snapshot and a full-chain SHA-256 fingerprint. The KP-only consequence endpoint
creates an idempotent draft, and ordinary proposal approval revalidates that fingerprint before
atomically committing supported narration and world effects.

The CoC7 opposed-check comparator is implemented as a pure, replay-tested ruleset operation and
its persisted API/UI orchestration now feeds the consequence fingerprint. Delivery status and the
machine-readable summary remain authoritative in `src/ai_kp/planning/capabilities.py`; the
complete state flow is documented in
[`CHECK_RESOLUTION.md`](CHECK_RESOLUTION.md).

## Adding or Updating a Capability

1. Add or update one catalogue entry with a stable ID, explicit audience, dependencies, at least one
   observable delivery check, and one or more stable `requirement_ids` from the PRD.
2. Keep every dependency as another catalogue ID. Registry validation rejects unknown, duplicate, self-referential, and cyclic entries.
3. Add `evidence_refs` only for real repository files that directly support the current status. A missing
   evidence reference must not be replaced with a planned path or prose claim.
4. Implement through the established Router-Service-Repository boundary; do not add a fake `501` endpoint solely to represent future work.
5. Add automated tests for the relevant API contract, authorization and visibility, transaction rollback, database restart/migration, and frontend build.
6. Run a real case using the intended GM/player/observer roles and, when applicable, a provider-discovered local model ID.
7. Change status only after all linked PRD acceptance is satisfied at the advertised scope. Update the
   summary and implementation audit when moving to or from `partial` so the planning UI remains honest.

Capabilities linked to `RPS-01..12` or `AC-LONG` cannot become `available` from component tests alone.
They require the UI, persona, multiplayer, restart and durability evidence specified by those IDs.

Documentation may explain stable architecture or testing procedures, but it should link back to the PRD
for product semantics and the catalogue for delivery state. This keeps requirements, status and evidence
separate without maintaining competing checklists.
