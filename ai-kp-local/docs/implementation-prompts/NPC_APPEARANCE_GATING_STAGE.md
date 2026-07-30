# Implementation Prompt — Deterministic NPC Appearance Gating

You are implementing the next vertical slice of the local-first AI KP platform.
Treat this document as a binding implementation and review checklist.

## Outcome

An NPC with a relationship-authorized prior encounter may only be offered or
materialized when deterministic world constraints do not prove that appearance
impossible. The system must explain why a candidate is allowed and must enforce
the same decision again inside the final write transaction.

## Required design

1. Keep stable investigator/NPC encounter history as the authorization boundary.
   A guessed global NPC identifier is never authority.
2. Add a versioned NPC availability profile with:
   - lifecycle state;
   - birth/death and active year bounds;
   - normalized location and profession tags;
   - a KP-only note.
3. Add a per-campaign reappearance policy and an append-only appearance ledger.
   The default campaign budget is one returning NPC. A campaign can explicitly
   configure another bounded value.
4. Use deterministic, explainable evaluation:
   - relationship failure, exhausted budget, impossible lifecycle, and known
     year contradiction are hard denials;
   - absent optional metadata is `needs_review`, not invented by AI;
   - a supplied location/profession contradiction is a hard denial only when
     the corresponding campaign policy is strict;
   - never calculate fake precision from free-text places.
5. Candidate responses expose public NPC identity, the calling campaign's own
   qualifying encounter evidence, decision state, reasons, warnings, and
   remaining campaign budget. Never expose NPC secrets or foreign campaign data.
6. Final materialization rechecks relationship, campaign policy, lifecycle,
   campaign time, actual placement location, profession context, and budget
   after `BEGIN IMMEDIATE`. Record the appearance ledger row in the same
   transaction as World Fact/NPC/map/encounter writes.
7. Add KP-only API operations for availability profiles and campaign policy.
   Validate all input with strict schemas and bounded arrays/text.
8. Update the frontend so KP can see why a returning NPC is eligible or needs
   review. The UI is explanatory only; server enforcement remains authoritative.
9. Add migration v28, base-schema parity, repository/application boundaries,
   API contract coverage, rollback/idempotency/concurrency-oriented tests, and
   documentation.

## Compatibility and restraint

- Continue supporting COC first, but keep names system-neutral.
- Historical campaign time may be a full SQLite ISO-8601 value or merely begin
  with a four-digit year. Reject malformed configured year bounds.
- Do not add a GIS dependency. Location tags are explicit aliases supplied by
  the KP. GeoJSON coordinates are a future extension.
- Existing NPCs without a profile remain possible but are labelled
  `needs_review`; do not silently fabricate profile data.
- Existing new-NPC materialization behavior must remain compatible.

## Verification

- Run focused backend and frontend tests.
- Run full backend, frontend, TypeScript, build, lint/format, migration
  integrity and `git diff --check` twice where practical.
- Extend and run the real HTTP scenario against the configured model on port
  8001: one returning NPC must pass; an impossible or over-budget NPC must not
  be materialized.
- Do not commit unless the user explicitly requests it.
