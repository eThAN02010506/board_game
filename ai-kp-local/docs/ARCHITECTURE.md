# Architecture

## Runtime Shape

The backend has explicit composition, transport, application, domain, ruleset, and adapter layers:

- `src/ai_kp/bootstrap/composition.py` is the composition root. It initializes the database once, installs CORS and error handlers, and mounts the domain routers. `bootstrap/settings.py` owns runtime settings. `api/main.py` remains the stable compatibility ASGI entry point.
- `src/ai_kp/api/routers/{system,campaigns,sessions,world,maps,modules,turns,realtime}.py` owns HTTP/WebSocket transport only. `dependencies.py`, `authz.py`, `errors.py`, and `schemas.py` centralize per-request repository lifetime, authentication, authorization, error mapping, and transport DTOs.
- `src/ai_kp/application/{campaign,world,session,map,map_image,turn}_service.py` owns use cases that coordinate validation, domain components, multiple writes, the transactional realtime outbox, and the local-model boundary. It has no FastAPI dependency.
- `src/ai_kp/application/ports/` defines the narrow persistence and AI-director contracts used by
  each service. Application code must not import `api`, `bootstrap`, or `infrastructure`;
  concrete adapters are supplied at the composition/delivery boundary.
- `src/ai_kp/platform/` owns ruleset-neutral memory, module, and scene logic. `director/` owns AI KP context and proposal orchestration. `rule_authoring/` owns extracted rule objects and deterministic validation/execution.
- `src/ai_kp/infrastructure/database/` owns SQLite mechanics, schema, ordered migrations, and feature repositories. Its `Repository` facade intentionally supplies one shared transaction boundary to current application services.
- `src/ai_kp/infrastructure/{knowledge,llm,images,modules,realtime,security}/` owns external and persistence adapters. These layers may depend inward on domain contracts; domain packages do not depend on these adapters.
- `src/ai_kp/application/realtime/` owns the authenticated connection lifecycle and pure wire-message
  decisions. `platform/realtime/ports.py` defines channel and event-store contracts;
  `infrastructure/realtime/` contains only Starlette, worker-thread, SQLite, and origin-check
  adapters. Transport close codes and public message shapes are regression-tested independently
  from those adapters.
- `src/ai_kp/planning/capabilities.py` is the only product capability catalogue. It records available, partial, and planned capabilities together with dependencies and acceptance criteria.
- `rulesets` is the application-facing executable-system boundary and currently registers only CoC7. Former `kp`, `maps`, `memory`, `modules`, `llm`, `security`, `realtime`, `rulebook`, `rules`, `characters`, `storage`, and selected `core` modules are compatibility paths only.

The React workspace follows the same separation:

- `apps/web/src/api` owns the HTTP client, credential bridge, and TypeScript API DTOs.
- `apps/web/src/session` owns browser-session credential and active-map persistence. It stores only opaque tokens and selection IDs, never authoritative campaign data.
- `apps/web/src/auth/credentials.tsx` initializes the API credential bridge and owns editable
  administrator/player-profile credentials; `app/providers.tsx` is the single provider
  composition entry.
- `apps/web/src/realtime/provider.tsx` is the frontend provider boundary for WebSocket lifecycle, cursor replay, event-to-refresh routing, burst coalescing, full sync, and stale campaign/session guards.
- `apps/web/src/features` contains the campaign, session, map, action, proposal, and capability-planning panels. `shared` contains presentation components reused by features.
- `apps/web/src/app/router.tsx` is the single route registry and browser-history adapter.
  `app/layout/AppLayout.tsx` owns the shared navigation and status shell.
- `apps/web/src/app/App.tsx` currently composes workspace state and feature callbacks. Further
  feature extraction must preserve current behavior and gain focused acceptance tests; the former
  root path is only a compatibility export.
- Top-level product areas use distinct history-backed paths (`/play`, `/campaigns`, `/investigators`, `/maps`, `/memory`, `/npcs`, `/rules`, `/modules`, and `/planning`). `/play` is the intentional composite exception: it places the controlled investigator and public party summaries beside the central map, with a separately scrollable action/chat column.

Future package boundaries are documented without pre-creating comment-only source files. Their
activation and incremental migration rules are tracked in
[`ARCHITECTURE_SKELETON.md`](ARCHITECTURE_SKELETON.md). A source module is created only with a
real vertical slice and focused tests; planned capability status lives in the capability catalogue.

## Router-Service-Repository Boundary

The dependency direction is `Router -> Application Service -> Repository/domain component`.

1. A router parses the transport DTO, resolves the authenticated identity, checks the resource's server-derived campaign and role, and maps the use-case result to HTTP. It must not embed SQL or construct model prompts.
2. An application service expresses one use case. It coordinates domain validation and all writes that must succeed together, including corresponding `realtime_events`. The model request in an AI turn runs before the short write transaction; after the model returns, the service begins the write phase and revalidates that the KP session is still active.
3. A repository owns SQL and row decoding. It does not know about FastAPI requests, browser state, or UI roles. Repositories participating in one use case share the connection supplied by the compatibility facade.
4. `api/dependencies.py:get_repo` commits once after a successful request, rolls back on every exception, and always closes the connection. Nested atomic operations such as proposal approval use SQLite savepoints so validation failure cannot leave partial events, memories, NPC changes, map moves, or outbox rows.

Plain read-only or single-table use cases still pass through a service when a domain policy is involved. A router may call a repository only for transport-level ownership lookup or a simple authenticated read where no orchestration is needed.

## SQLite Lifecycle and Migrations

`bootstrap/composition.py` runs `init_db()` at application construction. Request dependencies call
`connect()` but do not rerun schema initialization. File-backed databases use foreign keys, a
5-second busy timeout, WAL journal mode, and authoritative-data default `synchronous=FULL`;
`AI_KP_SQLITE_SYNCHRONOUS=NORMAL` is an explicit performance tradeoff. In-memory test databases
skip WAL. The default `data` directory and application-created runtime directories are restricted
to `0700`; SQLite files are repaired to `0600` whenever a connection is opened. Existing
user-selected parent directories, including a custom directory merely named `data`, are not
chmodded as a whole.

`infrastructure/database/schema.py:SCHEMA` creates the current schema for a new database. Existing databases are advanced by the numbered, ordered migrations under `infrastructure/database/migrations/`. Applied versions and names are recorded in `schema_migrations`; migration names are checked, future database versions fail closed, and each migration runs inside its own savepoint. Running `init_db()` again is idempotent.

At process construction, interrupted rule-object and module-knowledge LLM claims move from
`processing` back to `pending`. Both chunk types use monotonically increasing `attempt_count`
generations. Once an external LLM call returns, claim verification, extracted result writes,
failure audit writes, and the final status CAS occur under a short `BEGIN IMMEDIATE` transaction;
an older process cannot publish into a recovered and newly claimed attempt. Unexpected local
persistence failures roll back and best-effort fail the still-current generation. Startup closes
leftover running rule ingestion records as interrupted as well as recovering chunk claims. The
rule pipeline permits one running lease per source and stage: extraction claims atomically require
that lease, and extraction/index completion uses a `running` status CAS before publishing results.
Stale success and failure paths therefore cannot revive a recovered run or overwrite a replacement
index's source state. The module document worker separately recovers durable import jobs. Its own
`attempt_count` likewise guards progress, failure, and its single completion transaction, so a
stale worker cannot insert a module after another process has recovered the job.

## Rulebook knowledge boundary

`platform/knowledge/ports.py` defines source extraction and original-text index contracts.
`infrastructure/knowledge/` implements them with PDF extraction and MiniRAG;
`rule_authoring/` owns JSON rule validation and the closed deterministic execution DSL. SQLite
source chunks and MiniRAG files are deliberately dual
storage: the index can be rebuilt, while page evidence and validated objects remain authoritative.
The model can create candidates only. Schema, exact source citation and conflict checks determine
whether a candidate may be shown for review; they never promote model output by themselves.
Only an object reviewed by a local administrator who is also an authenticated KP, bound to its
exact source and ruleset identity, becomes `validated` and can reach the executor. This namespace
must not contain campaign memory, module spoilers, NPC state or character history. See
`docs/RULEBOOK_KNOWLEDGE.md`.

Rulebook knowledge is not an executable ruleset. `GET /rulesets` lists the explicit local
allow-list of deterministic engines. Campaign and investigator services resolve that registry;
application, API, and storage layers must not import concrete `rules` modules directly. The
incremental boundary and requirements for a future second system are documented in
[`RULESET_BOUNDARY.md`](RULESET_BOUNDARY.md).

The request transaction is committed only after the router and service finish. Any Python, SQLite, authorization, model-output, or domain validation exception triggers rollback. This includes business changes and their realtime outbox records, so a browser cannot receive an event for state that did not commit.

## Capability Catalogue

`GET /capabilities` serializes `src/ai_kp/planning/capabilities.py`. Passing `include_available=false` returns only partial and planned entries. The frontend planning/NPC/rules views consume this endpoint and deliberately render unavailable work as non-interactive planning cards.

This catalogue is the single source of truth for delivery status, phase, dependencies, and real-case acceptance. Documentation must link to the catalogue instead of copying a frozen roadmap table. It covers the whole planned surface, including seat invitations, document-based module import, ruleset plugins, semantic memory search, map reveal editing, and campaign backup/restore. See [ROADMAP.md](ROADMAP.md) for the update protocol rather than a second status list.

## Data Rules

- `events` is the append-only campaign history.
- `memories` is the curated recall layer.
- `campaign_npcs` records how an NPC relates to one campaign.
- Global NPC identity is stored once in `npcs`; this allows cross-module reuse.
- `modules` and `module_chunks` store imported KP material with visibility, spoiler metadata and
  PDF page or DOCX paragraph provenance. `module_import_jobs` is the durable queued/processing/
  completed/failed lifecycle; `module_assets` records every private image occurrence while
  `module_entities` and `module_entity_relations` form a narrow, provenance-bound directed graph.
  Every graph write references an approved module knowledge candidate; recursive traversal is a
  deterministic diagnostic and never mutates module source or world facts.
  content-addressed storage deduplicates identical bytes outside SQLite.
- `campaign_module_runs` is the campaign's explicit playthrough cursor. A partial unique index
  permits at most one active run per campaign. Scene, play pace, source-linked location, unlocked
  spoiler tags and a bounded 4 KiB state snapshot use optimistic `version` checks so two KP
  clients cannot silently overwrite one another. `module_run_scene_events` keeps the immutable
  scene-transition audit; `module_run_entity_states` projects per-playthrough clue/anchor state,
  while `module_run_entity_state_events` preserves every correction. Director intent analysis is
  read-only and may only return current-spoiler source text; all mutations remain KP-only. See
  [`SCENE_DIRECTOR.md`](SCENE_DIRECTOR.md).
- Module Canon/Anchor remains source-linked knowledge; generated world completion enters the
  existing proposal boundary and only becomes an append-only runtime fact after confirmation.
  See [`WORLD_EXPANSION.md`](WORLD_EXPANSION.md).
- `maps` stores stable identity, publication status, the current revision pointer and the selected public background; `map_revisions` stores canonical MapSpec JSON, validation output and content/layout hashes.
- `map_locations` and `map_routes` are the current compatible projection used by movement and older API fields. Deterministic SVG is rendered from the role-filtered current MapSpec instead of being trusted as an independent structure source.
- `map_assets` stores only validated image metadata, generation hash, prompt audit and a path relative to the controlled asset root. PNG/JPEG bytes use content-addressed storage outside SQLite and remain behind authenticated API access.
- `maps.status` is the publication boundary: generated maps start as `draft`; players can only discover/read `published` maps.
- `map_tokens` and `map_token_moves` store offline-table style piece placement and movement history. `map_tokens.version` provides optimistic concurrency control so a stale client cannot overwrite a newer move.
- `campaign_sessions` stores one active/closed play session per campaign and only the hash of its shared join code.
- `session_members` stores the server-authoritative `kp`/`player` role, optional controlled PC, revocation time, and only the hash of each Bearer access token.
- `session_seats` is the durable bridge between one campaign session, one stable `player_profile`, one current session member, and an optional reserved PC. Revoking it never deletes the profile or its investigators.
- Seat claims and investigator submit/review/assignment acquire a SQLite `BEGIN IMMEDIATE`
  write transaction before their first authoritative read. Conditional updates then verify the
  exact live status/revision being changed. Partial unique indexes enforce one active member and
  one claimed seat per stable profile in a session, plus one active member/seat reservation per PC.
  Migration refuses conflicting legacy live rows instead of silently revoking or reassigning them.
- `seat_invitations` stores only purpose-separated hashes. Each plaintext code belongs to exactly one seat and transitions once from `active` to `consumed` or `revoked`.
- `skill_checks` stores requested and resolved checks, raw percentile digits, selected result, difficulty threshold, precise ruleset/source identity, character state version and any original pre-override result. Replays dispatch by that stored identity rather than a current UI choice. `skill_check_actions` is its append-only transition audit.
- `player_actions` stores a player's queued action and the server-derived session/member/PC/map/location context. Its lifecycle is `submitted -> reviewed -> resolved/rejected`.
- `turn_proposals` stores validated narration, checks, events, memories, NPC updates, and map-move candidates; `proposal_actions` stores approval/rejection/override audit records.
- `context_assemblies` stores the exact prompt, included/excluded sources, visibility scope, and estimated token cost for an AI proposal.
- `realtime_events` is a transactional SQLite outbox. Every row belongs to a session and has a server-enforced `session`, `kp`, or targeted `member` audience.
- `realtime_tickets` stores only hashes of short-lived, single-use WebSocket tickets together with their member/session/campaign scope and consumption state.

## Character Sheet Ownership and Revision Boundary

The uploaded `COC空白卡.xlsx` is the field and interaction reference for investigator creation. It is not a database schema or a trusted calculation engine. [`CHARACTER_SHEET_MODEL.md`](CHARACTER_SHEET_MODEL.md) records the inspected sheet regions, canonical JSON shape, import audit, immutable revisions, per-campaign KP approval, runtime state, cross-campaign progression, page flow, and staged real-case tests.

The target model separates global player-owned `investigators`, immutable `investigator_revisions`, per-campaign `campaign_investigators`, and mutable `investigator_campaign_state`. Excel imports read allow-listed player inputs and retain provenance, but never execute formulas or macros. Derived values are recomputed by deterministic ruleset services. A campaign and its AI context use only that campaign's `approved_revision_id`; live HP, SAN, MP, temporary conditions, and inventory deltas do not mutate the approved revision.

Character preparation uses three explicit validation layers. `structure` reports malformed or
missing canonical inputs, `ruleset` applies deterministic creation budgets and prohibited
allocations, and `review_policy` identifies unusual but KP-reviewable choices. Every issue has a
stable code, path, severity and review flag. The API also derives the legacy `warnings` list from
that report so existing clients remain compatible. Pure normalization and derived-value
calculation do not make approval decisions; the application layer separately validates the KP's
approve/change-request command before the SQLite adapter persists it.

The current campaign-bound `player_characters` table remains a compatibility placeholder until the staged migration is implemented and verified. It must not be extended into the permanent cross-campaign identity model.

## Session and Authorization Flow

1. A local administrator or existing KP creates a campaign session. The service creates the KP member and retains the shared join code only as a legacy compatibility path.
2. The KP creates named seats, optionally reserves a campaign PC, and shares each one-time plaintext invitation only with its intended player.
3. A player claims the seat with an existing local player-profile token or creates a stable profile on first claim. Claiming consumes the invitation atomically and returns a session-scoped Bearer token.
4. A returning player uses the stable profile token to list owned seats and rotate/recover the Bearer token for an active claimed seat. The stable token is not itself session authorization.
5. Clients send the access token as `Authorization: Bearer ...`. API dependencies authenticate its hash against an active, non-revoked member in an active session.
6. Resource endpoints derive authority from server state; client-supplied view, actor, PC, campaign, location, and movement metadata never grant authority.
7. Revoking one seat invalidates only its member and invitation. Other players and codes are unaffected; closing the session invalidates every member token.

An approved investigator can only be assigned to an active member that already carries the same
stable `player_profile_id` as the investigator owner. KP cannot use an anonymous legacy join-code
member to take over a player-owned investigator. When that member owns a claimed seat, assignment
updates the member PC and `session_seats.assigned_pc_id` in the same transaction.

Join codes and access tokens use different domain-separated SHA-256 hashes, so the two credential types are not interchangeable. The server never returns stored hashes through public repository responses. Plaintext credentials exist only in create/join and explicit or revoke-triggered rotate responses; the browser keeps the active values in `sessionStorage`, never in a URL.

Seat revocation is session access revocation, not deletion of the stable player profile. It intentionally preserves that player's investigators and history. Durable bans and public-internet account recovery remain separate future security work.

## Browser Restore Flow

1. On startup the browser reads campaign-scoped access tokens from `sessionStorage`, validates one with `/auth/me`, and reloads the active session from the API. The opaque stable player-profile token is kept separately in `localStorage` so a closed tab can still recover owned active seats.
2. Saved map contents always come from SQLite through the API; the browser does not treat an in-memory React object or stored SVG as authoritative.
3. The browser stores only the last selected map ID under a `campaign + role` key. After identity restoration it lists maps through the corresponding KP/player view and reopens that ID only if the current role can still see it.
4. If the map was unpublished, the credential revoked, or the session closed, restore fails safely and stale UI state is cleared.

## Realtime Synchronization Flow

1. An authenticated HTTP client exchanges its Bearer token at `POST /realtime/tickets` for a default 30-second, single-use ticket. The response is `Cache-Control: no-store`; the Bearer token is never placed in a WebSocket URL or frame.
2. The browser connects to same-origin `/api/ws`. The server requires an allowed `Origin`, consumes the ticket atomically, and binds the socket to its stored session/campaign/member/role scope.
3. State mutations append `realtime_events` in the same SQLite transaction as the business write. Polling therefore observes committed world state and survives a process restart without relying on an in-memory broadcaster.
4. The server filters rows by `session`, `kp`, or target `member` audience before serialization. The browser receives an opaque event key as its cursor, never the SQLite sequence number.
5. The browser stores the last cursor per session member. It reconnects with exponential backoff, replays later visible events, and performs a safe full HTTP sync after connection readiness or an invalid cursor.
6. Events are mapped to identity, member, PC, action, proposal, or map refresh groups. A 100 ms queue coalesces bursts, and those requests are silent so background sync does not replace the user's operation log or global loading state.
7. Each poll revalidates the member and active session. Revocation or session closure sends an expiry signal and terminates the existing connection, rather than waiting for the next page reload.

The connection loop is deliberately split by responsibility: the transport adapter enforces
origin and frame boundaries, the application session controls authentication/replay/heartbeat
lifecycle, and pure message functions decide protocol responses. New client message types must be
added to the message layer rather than branching in the Starlette adapter.

In development, Vite proxies both HTTP and WebSocket `/api` traffic. `VITE_BACKEND_TARGET` can point that proxy at a different test backend while preserving a same-origin browser connection.

## Map Publication Flow

1. KP generation converts the request and campaign time into `map-spec.v1`, validates structure, geometry, connectivity, era context and required-element coverage, then persists revision 1 and the compatible locations/routes projection with `status=draft`.
2. The full KP response is rendered from the current MapSpec. A player response first projects the spec to `player/table`, removes scene brief, provenance and KP notes, then renders a fresh safe SVG.
3. Optional image generation builds its prompt from the player-safe projection only. It cannot see KP-only elements, clues, tokens or hidden routes. A generation hash makes identical provider/model/seed/spec requests reusable.
4. Provider output must be Base64 PNG/JPEG within byte and pixel limits. The file is written atomically to content-addressed storage; creating a candidate does not change the selected background or publication state.
5. KP previews candidates and explicitly selects one. Only the selected public asset can be fetched by players, and only while the map is published; unselected assets remain KP-only.
6. KP can inspect draft maps, place tokens, and publish/unpublish explicitly. Player map lists include only `published` maps; direct access to a draft returns not found.
7. Background image, safe structure SVG, labels and tokens share one SVG coordinate system. If the asset cannot be loaded, the client removes only the image layer and keeps the deterministic SVG playable.
8. Player movement requires a visible current location, destination and route, and the token must be bound to the authenticated PC. Movement history omits KP-only metadata and hidden locations.

Map images are decorative, never authoritative. “Every requested element exists” is proven by the
MapSpec coverage report and deterministic overlay, not by visual inspection of diffusion-model
pixels. Historical plausibility is constrained by the era profile and forbidden list, while
aesthetic acceptance remains an explicit KP decision. See
[`MAP_GENERATION.md`](MAP_GENERATION.md).

## Player Action and Proposal Flow

1. A player submits action text and an optional controlled token/map. The service derives PC and location from the authenticated membership and visible token state. A per-member `client_action_id` makes browser retries idempotent.
2. The action is stored as `submitted`; only that member and the KP can read it.
3. When the KP creates a manual proposal or `/kp/turn` from that queue item, it is atomically linked and becomes `reviewed`. One submitted action cannot be claimed twice.
4. Approving the linked draft atomically applies the validated world changes and marks the action `resolved`.
5. Rejecting the linked draft applies no world changes and marks the action `rejected`.

The player-action response does not embed the linked proposal, KP notes, secret context, or final prompt.

## World Fact Ledger

World facts reuse the append-only `events` stream instead of creating a second mutable source of
truth. Strict `world_fact.asserted` and `world_fact.retconned` envelopes carry a stable fact key,
revision, current-head link, epistemic category, PC scope, evidence event IDs, and source
reference. A pure projector validates the chain and derives current heads after role and PC
filtering.

Generic event writes cannot use the reserved `world_fact.*` namespace. Corrections require the
caller's expected head event ID, append a new revision, and retain the old event. Character
beliefs are stored KP-only in the generic event stream because generic readers do not understand
PC ownership; the fact projector alone exposes them to their owning PC. AI context receives only
current visible heads and is explicitly told that beliefs, rumors, and AI hypotheses are not
canonical facts. See [`WORLD_FACT_LEDGER.md`](WORLD_FACT_LEDGER.md).

Proposal approval is split into three boundaries. `platform/resolution/proposals.py` owns the
ruleset-neutral invariant that unresolved checks cannot carry precommitted world effects.
`application/play/proposal_approval.py` validates each requested check against the campaign's
pinned ruleset and produces immutable check plans. The SQLite adapter claims the draft and applies
narration, explicit events, memories, NPC updates and map moves through separate effect handlers
inside one savepoint. The unresolved-check invariant is rechecked after the draft is claimed, so
direct repository use cannot bypass the application policy; any handler failure restores the
proposal and all affected world state.

## AI Turn Flow

1. `ContextBuilder` reads campaign time, the exact investigator revision approved for this
   campaign plus its mutable runtime state, relevant memories, eligible old NPCs, recent events,
   the explicitly active module run, and only that module's reviewed knowledge and chunks.
2. The active run pins scene, spoiler tags and bounded runtime state. A caller may narrow its
   spoiler set but cannot widen it. Knowledge, graph entities and graph relations must preserve
   every source visibility/spoiler boundary; retrieval repeats those checks for legacy data.
3. Player-scope projections require an identity-bound PC and only use published maps. Visibility,
   spoiler activation, relevance, item limits, and context budget determine which optional
   sources are included; campaign time, approved character core and current module-run core are
   never silently dropped.
4. The model produces strict JSON. One repair attempt is allowed when a local model returns malformed output.
5. A `turn_proposal` and its `context_assembly` are saved together for inspection.
6. Human KP approval atomically applies events, curated memories, NPC relationship updates, and validated map moves. Any invalid cross-campaign reference rolls the entire approval back.

For a real local-model test, first query the provider's OpenAI-compatible `/v1/models` endpoint and use an ID returned in `data[].id` as `AI_KP_LLM_MODEL`. A guessed `.gguf` filename is not an API capability check. The provider is considered verified only after model discovery, `/v1/chat/completions`, and a complete validated KP proposal flow all succeed.

## Rules Authority and AI Boundary

[`RULES_REFERENCE.md`](RULES_REFERENCE.md) identifies the user-provided CoC7 Keeper Rulebook by version and content hash, maps implementation areas to printed and PDF pages, and defines the required source labels and delivery checks. The PDF remains a local reference and is not committed or served by this project.

Game mechanics must run in deterministic ruleset services rather than in model prose. The model may suggest a check and narrate a validated outcome, but it cannot authoritatively calculate or directly commit dice thresholds, damage, healing, sanity, growth, chase movement, or other rule-dependent state. A mechanical result stores its ruleset version, source reference, normalized inputs, raw dice, outcome, affected state version, and any audited KP override.

The rule-object executor is a closed interpreter, not an `eval` surface. `runtime.py` owns field,
operand, condition and effect primitives; `execution_strategies.py` owns lookup-table and
condition/effect traversal; `engine.py` only validates required inputs and dispatches the declared
execution kind. Missing targets, incompatible operands, reference-only rules and unmatched lookup
rows fail closed with `RuleExecutionError`.

The first implemented mechanics slice is documented in [`CHECK_RESOLUTION.md`](CHECK_RESOLUTION.md). Its pure resolver is separate from secure random generation and physical-dice input; both paths persist identical replay data. Check-only proposals cannot carry world effects before resolution.

Core rules, book-optional rules, campaign house rules, one-off Keeper rulings, and platform workflow policies are distinct sources. Optional and house rules require explicit campaign configuration. Product policies such as character-sheet review must not be presented as though they came from the rulebook.

## Trust Boundary

Campaign sessions now enforce `kp`/`player` authorization on the server. Frontend role-specific controls are only usability; the API remains the security boundary. Cross-campaign resources, KP notes, proposal/context inspection, inactive spoilers, draft maps, other PCs' private sheets, and other players' actions are denied or filtered independently of the UI.

`AI_KP_LOCAL_ADMIN_ENABLED=true` is strictly a loopback development bootstrap. It checks the
actual socket peer and ignores forwarded-address headers. `deployment_mode=lan` forcibly disables
this bypass even if the environment still says `LOCAL_ADMIN_ENABLED=true`, because a reverse proxy
commonly appears as loopback. For LAN, container, tunnel, or reverse-proxy use, configure:

```env
AI_KP_ADMIN_TOKEN=<long-random-secret>
AI_KP_CORS_ORIGINS=https://<frontend-origin>
```

The admin token is sent as `X-AI-KP-Admin-Token`. A reverse proxy commonly makes all upstream requests appear loopback, which is why leaving local admin enabled behind a proxy defeats the intended boundary.

An active KP credential can be reissued through the local-administrator recovery endpoint when its
plaintext token is lost. Recovery rotates the existing KP member's token hash, invalidates the old
Bearer token, preserves the session/member IDs and campaign data, and emits an audit event. It does
not create a second KP or close the active session.

TLS is outside the application and is mandatory at the reverse proxy. Without HTTPS, Bearer
tokens, seat invitations, compatibility join codes, and the admin token are observable on the
network. Stable local player identities and per-seat invitations are implemented, but this MVP
still has no password accounts, token expiry, brute-force lockout, durable identity bans, or
trusted-proxy policy. It is not ready for direct public-internet exposure. Future imported or
AI-authored SVG also needs sanitization and a restrictive CSP before it can be treated as
untrusted content.

The first token estimator is intentionally local and model-independent. A provider-specific tokenizer can replace it without changing the stored audit format.

## Capability Delivery Rule

There is no static "next implementation steps" list in this document. Priorities and dependency order are read from `src/ai_kp/planning/capabilities.py` through `GET /capabilities`; [ROADMAP.md](ROADMAP.md) describes how an entry progresses from planned to partial to available.

A capability may be marked available only after its server-side authorization, persistence/restart behavior, rollback behavior, frontend role boundary, automated regression tests, and stated real-case acceptance all pass. A directory, data model, helper, or placeholder panel by itself is not evidence that the user-facing capability is complete.
