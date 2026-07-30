# Implementation Prompt: Stable Investigator NPC Reappearance

You are modifying AI KP Local after the approved-candidate contact materialization
slice. Implement the next vertical slice: an existing NPC may reappear in a new
campaign only because a stable investigator in that campaign has auditable prior
contact with the NPC.

## Product invariant

An NPC is a global identity. Its relationship to a campaign, its appearance as a
map token, and each investigator's memories of it are separate projections.
Knowing or guessing a global NPC ID is never authorization to read or reuse it.

## Required flow

1. When a KP confirms an actual world-expansion contact, the command may name one
   or more approved stable investigators participating in that contact.
2. In the same atomic materialization transaction, append a source-linked
   investigator/NPC encounter record containing only:
   - the stable investigator ID;
   - NPC ID;
   - campaign and source encounter event;
   - in-world time;
   - a concise description of what they did together.
3. Add a KP-only candidate endpoint for the current campaign. It may return an
   NPC from another campaign only when:
   - a currently approved investigator in the current campaign has a prior
     encounter with that NPC;
   - the encounter belongs to the same investigator ID, not merely the same
     player display name or legacy PC ID;
   - the NPC is not already linked to the current campaign;
   - optional name/profession/location search still leaves an auditable reason.
4. Candidate responses expose the NPC's public identity and the qualifying
   investigator's own encounter summary. Never expose another investigator's
   memories, source campaign secrets, secret NPC notes, tokens, or unrelated
   campaign state.
5. The contact materialization command may reference an existing NPC only if it
   is already linked to this campaign or appears in the server-computed candidate
   set for one of the submitted, approved participant investigator IDs.
6. Selecting a candidate and confirming actual contact links the NPC to the new
   campaign, records the new encounter, appends strict facts, and optionally
   places/moves its token at an existing reviewed MapSpec location in one
   idempotent transaction.

## Architecture constraints

- Use an append-oriented encounter table plus indexes and foreign keys; do not
  put cross-campaign memory into model context or mutable JSON blobs.
- Enforce authorization again inside the transaction, not only in the UI or GET
  endpoint.
- Keep the application service dependent on a Protocol, not SQLite modules.
- Add schema migration v27 and update the base schema and migration registry.
- Preserve all existing API behavior. Participant IDs are optional when no NPC
  is involved, but any recorded NPC contact must have at least one approved
  stable investigator.
- Use strict Pydantic and TypeScript contracts with bounded arrays and strings.
- The UI must offer “new NPC” and “known NPC” modes. Known NPC choices come only
  from the server candidate endpoint and explain which investigator remembers
  the NPC and why.
- Do not add automatic AI selection. Human KP still confirms the actual contact.

## Required verification

- Migration upgrade and fresh-schema tests.
- Atomic success covering fact + reused NPC + campaign link + map token + new
  encounter.
- Rollback test proving an invalid map location leaves no new campaign link,
  encounter, event, fact, receipt, or token.
- Permission test proving a guessed foreign NPC ID without qualifying history is
  rejected.
- Isolation test proving one investigator's encounter does not authorize a
  different investigator owned by the same player.
- Idempotent retry does not duplicate encounter history.
- API contract, frontend interaction, TypeScript, frontend suite/build, Ruff,
  full backend suite, and a real HTTP case.

## Completion rule

Do not claim success because the candidate endpoint lists an NPC. Success means a
KP can select a server-authorized prior NPC, confirm that players actually met
them, and restart with the new campaign link, fact provenance, encounter history,
and optional map token intact.
