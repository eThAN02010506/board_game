# Architecture

## Runtime Shape

The backend has three explicit delivery layers plus domain components:

- `src/ai_kp/api/app.py` is the composition root. It initializes the database once, installs CORS and error handlers, and mounts the domain routers. `api/main.py` remains the stable compatibility entry point for `ai_kp.api.main:app` and `create_app`.
- `src/ai_kp/api/routers/{system,campaigns,sessions,world,maps,turns,realtime}.py` owns HTTP/WebSocket transport only. `dependencies.py`, `authz.py`, `errors.py`, and `schemas.py` centralize per-request repository lifetime, authentication, authorization, error mapping, and transport DTOs.
- `src/ai_kp/application/{campaign,world,session,map,turn}_service.py` owns use cases that coordinate validation, domain components, multiple writes, the transactional realtime outbox, and the local-model boundary. It has no FastAPI dependency.
- `src/ai_kp/storage/sqlite.py` and `rows.py` provide shared SQLite mechanics. `storage/repositories/world.py` and `turns.py` contain domain-specific SQL, while the existing map, context, security, and realtime repositories remain beside their domains. `storage/migrations/` contains ordered schema migrations.
- `src/ai_kp/core/repository.py` is a compatibility facade that composes the feature repositories over one SQLite connection. It is an intentional transaction boundary for existing services and tests, not an aggregate repository waiting to be split again. `core/db.py` remains the schema/bootstrap boundary; `core/config.py` and `core/ids.py` contain configuration and ID generation.
- `src/ai_kp/planning/capabilities.py` is the only product capability catalogue. It records available, partial, and planned capabilities together with dependencies and acceptance criteria.
- `src/ai_kp/kp`, `maps`, `memory`, `modules`, `llm`, `security`, and `realtime` contain the working domain components. `rules` and `human_kp` contain foundations that are only exposed when their catalogue entries say they are ready; their presence alone is not a completed feature claim.

The React workspace follows the same separation:

- `apps/web/src/api` owns the HTTP client, credential bridge, and TypeScript API DTOs.
- `apps/web/src/session` owns browser-session credential and active-map persistence. It stores only opaque tokens and selection IDs, never authoritative campaign data.
- `apps/web/src/hooks/useWorkspaceRealtime.ts` is the frontend provider boundary for WebSocket lifecycle, cursor replay, event-to-refresh routing, burst coalescing, full sync, and stale campaign/session guards.
- `apps/web/src/features` contains the campaign, session, map, action, proposal, and capability-planning panels. `shared` contains presentation components reused by features.
- `apps/web/src/App.tsx` composes workspace state and feature callbacks; transport, session persistence, realtime-provider logic, and feature rendering are kept in their dedicated modules.

## Router-Service-Repository Boundary

The dependency direction is `Router -> Application Service -> Repository/domain component`.

1. A router parses the transport DTO, resolves the authenticated identity, checks the resource's server-derived campaign and role, and maps the use-case result to HTTP. It must not embed SQL or construct model prompts.
2. An application service expresses one use case. It coordinates domain validation and all writes that must succeed together, including corresponding `realtime_events`. The model request in an AI turn runs before the short write transaction; after the model returns, the service begins the write phase and revalidates that the KP session is still active.
3. A repository owns SQL and row decoding. It does not know about FastAPI requests, browser state, or UI roles. Repositories participating in one use case share the connection supplied by the compatibility facade.
4. `api/dependencies.py:get_repo` commits once after a successful request, rolls back on every exception, and always closes the connection. Nested atomic operations such as proposal approval use SQLite savepoints so validation failure cannot leave partial events, memories, NPC changes, map moves, or outbox rows.

Plain read-only or single-table use cases still pass through a service when a domain policy is involved. A router may call a repository only for transport-level ownership lookup or a simple authenticated read where no orchestration is needed.

## SQLite Lifecycle and Migrations

`api/app.py` runs `init_db()` at application construction. Request dependencies call `connect()` but do not rerun schema initialization. File-backed databases use foreign keys, a 5-second busy timeout, WAL journal mode, and `synchronous=NORMAL`; in-memory test databases skip WAL.

`core/db.py:SCHEMA` creates the current schema for a new database. Existing databases are advanced by the numbered, ordered migrations under `storage/migrations/`. Applied versions and names are recorded in `schema_migrations`; migration names are checked, future database versions fail closed, and each migration runs inside its own savepoint. Running `init_db()` again is idempotent. The additive migration helpers are compatibility code for real pre-migration SQLite files and must not be removed as apparent duplication.

The request transaction is committed only after the router and service finish. Any Python, SQLite, authorization, model-output, or domain validation exception triggers rollback. This includes business changes and their realtime outbox records, so a browser cannot receive an event for state that did not commit.

## Capability Catalogue

`GET /capabilities` serializes `src/ai_kp/planning/capabilities.py`. Passing `include_available=false` returns only partial and planned entries. The frontend planning/NPC/rules views consume this endpoint and deliberately render unavailable work as non-interactive planning cards.

This catalogue is the single source of truth for delivery status, phase, dependencies, and real-case acceptance. Documentation must link to the catalogue instead of copying a frozen roadmap table. It covers the whole planned surface, including seat invitations, document-based module import, ruleset plugins, semantic memory search, map reveal editing, and campaign backup/restore. See [ROADMAP.md](ROADMAP.md) for the update protocol rather than a second status list.

## Data Rules

- `events` is the append-only campaign history.
- `memories` is the curated recall layer.
- `campaign_npcs` records how an NPC relates to one campaign.
- Global NPC identity is stored once in `npcs`; this allows cross-module reuse.
- `modules` and `module_chunks` store imported KP material with visibility and spoiler metadata.
- `maps`, `map_locations`, and `map_routes` persist AI-generated SVG and the structured location graph as reusable campaign state in SQLite.
- `maps.status` is the publication boundary: generated maps start as `draft`; players can only discover/read `published` maps.
- `map_tokens` and `map_token_moves` store offline-table style piece placement and movement history. `map_tokens.version` provides optimistic concurrency control so a stale client cannot overwrite a newer move.
- `campaign_sessions` stores one active/closed play session per campaign and only the hash of its shared join code.
- `session_members` stores the server-authoritative `kp`/`player` role, optional controlled PC, revocation time, and only the hash of each Bearer access token.
- `player_actions` stores a player's queued action and the server-derived session/member/PC/map/location context. Its lifecycle is `submitted -> reviewed -> resolved/rejected`.
- `turn_proposals` stores validated narration, checks, events, memories, NPC updates, and map-move candidates; `proposal_actions` stores approval/rejection/override audit records.
- `context_assemblies` stores the exact prompt, included/excluded sources, visibility scope, and estimated token cost for an AI proposal.
- `realtime_events` is a transactional SQLite outbox. Every row belongs to a session and has a server-enforced `session`, `kp`, or targeted `member` audience.
- `realtime_tickets` stores only hashes of short-lived, single-use WebSocket tickets together with their member/session/campaign scope and consumption state.

## Session and Authorization Flow

1. A local administrator or existing KP creates a campaign session. The service creates the KP member and returns the KP access token plus shared join code once.
2. A player exchanges the active join code for an independent player member and access token, optionally claiming an unassigned PC. The KP can later bind or rebind that member to a campaign PC.
3. Clients send the access token as `Authorization: Bearer ...`. API dependencies authenticate its hash against an active, non-revoked member in an active session.
4. Resource endpoints derive the campaign from the stored map/token/proposal/action and compare it with the authenticated campaign. Client-supplied view, actor, PC, campaign, location, and movement metadata never grant authority.
5. KP-only operations include module import, secret/KP memory, NPC candidate retrieval, map generation/publication, token placement, proposal/context inspection, AI turns, approvals, member management, and the full player-action queue.
6. Players can read their campaign's player-safe material and published maps, search their own character memory, move/inspect only their bound PC token, and submit/read only their own actions.
7. Revoking a player invalidates that token immediately and rotates the shared join code. Closing the session invalidates every member token because authentication requires an active session.

Join codes and access tokens use different domain-separated SHA-256 hashes, so the two credential types are not interchangeable. The server never returns stored hashes through public repository responses. Plaintext credentials exist only in create/join and explicit or revoke-triggered rotate responses; the browser keeps the active values in `sessionStorage`, never in a URL.

Revocation is credential revocation, not identity banning. Because the MVP has one shared join code and no user accounts, a revoked person who obtains the newly rotated code can join again as a new member. Per-seat invitations or accounts are required for durable identity-level bans.

## Browser Restore Flow

1. On startup the browser reads campaign-scoped access tokens from `sessionStorage`, validates one with `/auth/me`, and reloads the active session from the API.
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

In development, Vite proxies both HTTP and WebSocket `/api` traffic. `VITE_BACKEND_TARGET` can point that proxy at a different test backend while preserving a same-origin browser connection.

## Map Publication Flow

1. KP generation persists structured locations/routes plus SVG with `status=draft`.
2. KP can inspect draft maps, place tokens, and publish/unpublish explicitly.
3. Player map lists include only `published` maps; direct access to a draft returns not found.
4. Player responses include only `player`/`table` locations, routes, and tokens. Player movement additionally requires a visible current location, destination, and route, and the token must be bound to the authenticated PC.
5. Player movement history omits KP-only movement metadata and hidden locations.

## Player Action and Proposal Flow

1. A player submits action text and an optional controlled token/map. The service derives PC and location from the authenticated membership and visible token state. A per-member `client_action_id` makes browser retries idempotent.
2. The action is stored as `submitted`; only that member and the KP can read it.
3. When the KP creates a manual proposal or `/kp/turn` from that queue item, it is atomically linked and becomes `reviewed`. One submitted action cannot be claimed twice.
4. Approving the linked draft atomically applies the validated world changes and marks the action `resolved`.
5. Rejecting the linked draft applies no world changes and marks the action `rejected`.

The player-action response does not embed the linked proposal, KP notes, secret context, or final prompt.

## AI Turn Flow

1. `ContextBuilder` reads campaign time, relevant memories, eligible old NPCs, recent events, and active module chunks.
2. Visibility, spoiler activation, relevance, item limits, and context budget determine which sources are included.
3. The model produces strict JSON. One repair attempt is allowed when a local model returns malformed output.
4. A `turn_proposal` and its `context_assembly` are saved together for inspection.
5. Human KP approval atomically applies events, curated memories, NPC relationship updates, and validated map moves. Any invalid cross-campaign reference rolls the entire approval back.

For a real local-model test, first query the provider's OpenAI-compatible `/v1/models` endpoint and use an ID returned in `data[].id` as `AI_KP_LLM_MODEL`. A guessed `.gguf` filename is not an API capability check. The provider is considered verified only after model discovery, `/v1/chat/completions`, and a complete validated KP proposal flow all succeed.

## Trust Boundary

Campaign sessions now enforce `kp`/`player` authorization on the server. Frontend role-specific controls are only usability; the API remains the security boundary. Cross-campaign resources, KP notes, proposal/context inspection, inactive spoilers, draft maps, other PCs' private sheets, and other players' actions are denied or filtered independently of the UI.

`AI_KP_LOCAL_ADMIN_ENABLED=true` is strictly a loopback development bootstrap. It checks the actual socket peer and ignores forwarded-address headers. For LAN, container, tunnel, or reverse-proxy use, set:

```env
AI_KP_LOCAL_ADMIN_ENABLED=false
AI_KP_ADMIN_TOKEN=<long-random-secret>
AI_KP_CORS_ORIGINS=https://<frontend-origin>
```

The admin token is sent as `X-AI-KP-Admin-Token`. A reverse proxy commonly makes all upstream requests appear loopback, which is why leaving local admin enabled behind a proxy defeats the intended boundary.

TLS is outside the application and is mandatory at the reverse proxy. Without HTTPS, Bearer tokens, join codes, and the admin token are observable on the network. This MVP has no accounts, per-seat invitations, token expiry, rate limiting, brute-force lockout, durable identity bans, or trusted-proxy policy. It is not ready for direct public-internet exposure. Future imported/AI-authored SVG also needs sanitization and a restrictive CSP before it can be treated as untrusted content.

The first token estimator is intentionally local and model-independent. A provider-specific tokenizer can replace it without changing the stored audit format.

## Capability Delivery Rule

There is no static "next implementation steps" list in this document. Priorities and dependency order are read from `src/ai_kp/planning/capabilities.py` through `GET /capabilities`; [ROADMAP.md](ROADMAP.md) describes how an entry progresses from planned to partial to available.

A capability may be marked available only after its server-side authorization, persistence/restart behavior, rollback behavior, frontend role boundary, automated regression tests, and stated real-case acceptance all pass. A directory, data model, helper, or placeholder panel by itself is not evidence that the user-facing capability is complete.
