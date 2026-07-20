# Capability Roadmap Protocol

This file explains how the roadmap is maintained; it is not a second roadmap snapshot.

## Authoritative Sources

- `src/ai_kp/planning/capabilities.py` is the only source of capability IDs, labels, delivery status, phase, audience, dependencies, summaries, and acceptance criteria.
- `GET /capabilities` is the machine-readable view used by clients. Use `GET /capabilities?include_available=false` to inspect only work that is not fully delivered.
- The browser's "功能规划" view reads that endpoint at runtime. It must not keep its own status constants or pretend a planned endpoint exists.

The catalogue covers both current MVP foundations and the complete planned surface. That includes the newer tracks for per-seat invitations, multi-format module documents, ruleset plugins, local semantic memory search, map reveal editing, and campaign backup/restore. Their current status and dependency graph must be read from the catalogue rather than copied here.

## Status Meaning

- `planned`: the acceptance boundary is defined, but users cannot complete the end-to-end workflow.
- `partial`: a tested foundation exists, but at least one acceptance requirement or user-facing step is missing.
- `available`: the full catalogue acceptance criteria pass through the supported API and UI workflow.

Source files or placeholder UI alone do not justify `partial` or `available`. A partial implementation must describe exactly what works and what remains in the catalogue summary.

## Adding or Updating a Capability

1. Add or update one catalogue entry with a stable ID, explicit audience, dependencies, and at least one observable acceptance criterion.
2. Keep every dependency as another catalogue ID. Registry validation rejects unknown, duplicate, self-referential, and cyclic entries.
3. Implement through the established Router-Service-Repository boundary; do not add a fake `501` endpoint solely to represent future work.
4. Add automated tests for the relevant API contract, authorization and visibility, transaction rollback, database restart/migration, and frontend build.
5. Run a real case using the intended KP/player roles and, when applicable, a provider-discovered local model ID.
6. Change status only after the acceptance criterion is satisfied. Update the summary when moving to or from `partial` so the planning UI remains honest.

Documentation may explain stable architecture or testing procedures, but it should link back to the catalogue for delivery state. This rule keeps the API, UI, and planning record synchronized without maintaining duplicate checklists.
