# Local / online multiplayer readiness

## 2026-09-09: realtime read authority

The target remains a Steam game with local and online multiplayer, not an MMO.
The browser is the current test client. AI KP and human KP must share the same
authoritative application services; model output is not permission to mutate a room.

### Existing paths inspected

- `application/realtime/session.py` authenticates a one-use ticket, revalidates
  active membership during the connection loop, and resumes using opaque cursors.
- `infrastructure/realtime/outbox.py` persists audience-scoped events and resolves
  only visible cursors. Observer events use a restricted public allowlist.
- `apps/web/src/realtime.ts` obtains a fresh ticket on reconnect, checks room/member
  identity on ready, checks room scope on events, and uses bounded backoff.
- `apps/web/src/realtime/provider.tsx` refreshes authoritative resources on ready
  and routes event notifications to resource refreshes.

### Change and rationale

Event reads previously checked live membership but trusted the supplied role.
Cursor resolution trusted the supplied role without checking live membership.
Both queries now require a non-revoked member in the requested active session,
matching campaign and matching current database role. A cached or mismatched
role fails closed; it is not silently upgraded to new privileges.

This is query-time authorization in the existing SQLite adapter, not another
multiplayer engine. No schema migration, dependency, or alternate AI/human path
was added. Handshake-only authorization and periodic connection checks are not
sufficient for an authority change committed before an event query. This choice
follows the session revalidation and message-level authorization guidance in the
[OWASP WebSocket Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/WebSocket_Security_Cheat_Sheet.html).

`tests/test_realtime_authority.py` covers public/KP/member/observer audiences,
forged roles, absent members, invalid roles, and demotion/revocation/session
closure committed by a second database connection before a stale KP read.
Both event listing and cursor resolution are checked.

### Boundaries and next work

This guard cannot retract frames already read or delivered before revocation.
It does not establish instantaneous revocation of an in-flight batch. A future
role-transfer feature must also explicitly invalidate privileged client caches.

This is a bounded security checkpoint, not full multiplayer acceptance. Next:

1. Audit command ownership, idempotency and expected-version checks under two
   concurrent clients; verify rejection and recovery, not just happy-path writes.
2. Audit reconnect refresh coverage for newly added world entity state events.
3. Keep scenario compilation separate from the live room: stage bounded model
   candidates, validate fixed identities server-side, and explicitly approve
   contract replacement without overwriting a running session.
4. Exercise two real browser clients through reconnect, secret visibility,
   conflicting actions and AI/human KP shared execution.
5. Separately validate host deployment and eventual Steam transport/security.

No local-model inference or full UI playthrough is required to prove these SQL
authorization properties; neither is claimed by this checkpoint.
