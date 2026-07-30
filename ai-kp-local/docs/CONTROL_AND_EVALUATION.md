# Control, reveal, map editing and evaluation

This document records the durable boundaries implemented after the first scene-director slice.
Delivery status remains authoritative in `src/ai_kp/planning/capabilities.py`.

## Human control

Every active module run stores one director control mode:

- `ai_assist`: model-backed turn, consequence and world-expansion generation is allowed;
- `safety_paused`: model-backed progression is rejected with HTTP 409;
- `human_kp`: the same model boundary is closed while manual KP scene and entity operations remain
  available.

Transitions require the current module-run version and a reason. They append
`module_run_control_events`; a per-run monotonic sequence preserves exact audit order even when
multiple transitions share one timestamp. Restart does not silently return control to AI.

## Player handouts

`campaign_handouts` stores draft, revealed or withdrawn handouts. KP may pin and link a handout to
a fact, NPC, map, location or module entity. Players query only revealed rows through a server-side
projection. `PUT /handouts/{id}/read` is idempotent per session member, while KP can inspect read
receipts. Player responses omit creator, revealer and other-seat receipt metadata.

## Map revisions and fog

Editing a MapSpec validates the complete structure, checks the expected current revision and writes
a new immutable revision. Stable element IDs preserve existing locations and token references.
Removing an occupied location fails. Routes are rebuilt in the same transaction. Fog polygons are
bounded to the map canvas and tied to the current revision; players receive only hidden regions,
which render above map structure and tokens.

## Simulation and observability

Simulation cases are immutable definitions. Each replay records definition hash, runner version,
trajectory, metrics and result fingerprint. The first runner supports audience-scoped emissions,
explicit reveal and player-visible/player-hidden assertions.

The debug console exposes:

- request p50/p95/p99 latency;
- actual `BEGIN IMMEDIATE` lock-wait p50/p95/p99/max;
- retrieval recall/MRR and forbidden-hit reports;
- bounded warning/critical alerts for slow locks, low recall and secret leakage.

These metrics are local operational diagnostics, not product analytics. They intentionally contain
hashes and counts instead of secret text.

## Security regression

`tests/test_security_matrix.py` is the machine-readable authorization matrix. It covers anonymous,
player, same-campaign KP and other-campaign KP views, including tenant-ID substitution. Cross-tenant
requests are denied with an opaque not-found response where resource lookup is scoped, or a generic
forbidden response at older service boundaries; neither response returns the object. The same suite
injects an exception after a database write and verifies that the request dependency rolls the
transaction back.

## Sources used for the design

- [Chaosium opposed-roll rules](https://callofcthulhuwiki.chaosium.com/rules/opposed-skill-rolls.html)
- [OWASP authorization testing automation](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Testing_Automation_Cheat_Sheet.html)
- [OWASP object-level authorization](https://owasp.org/API-Security/editions/2023/en/0xa1-broken-object-level-authorization/)
- [Google Cloud human-in-the-loop pattern](https://docs.cloud.google.com/architecture/choose-design-pattern-agentic-ai-system)
- [Microsoft durable human-in-the-loop workflows](https://learn.microsoft.com/en-us/azure/durable-task/sdks/durable-agents-microsoft-agent-framework)
- [NIST automated benchmark evaluation guidance](https://www.nist.gov/news-events/news/2026/01/towards-best-practices-automated-benchmark-evaluations)
- [SQLite transactions](https://www.sqlite.org/lang_transaction.html)
