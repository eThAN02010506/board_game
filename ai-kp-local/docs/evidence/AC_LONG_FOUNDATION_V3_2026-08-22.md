# AC-LONG foundation evidence v3 — 2026-08-22

This is immutable evidence for the non-UI foundation run. It is not an
`AC-LONG` pass and must not be presented as a validated long-running product.

## Source and artifacts

- Source commit: `82a7597d5e7ad36fed95c10074608d943421ac9c`
- Evidence JSON: [`AC_LONG_FOUNDATION_V3_2026-08-22.json`](AC_LONG_FOUNDATION_V3_2026-08-22.json)
- JSON SHA-256: `2dc5fbef09b5a4113352185daadb9323c56c9158178ffcd4a200ef6aec736e5f`
- Edge-test transcript SHA-256: `5f8cba11a7cbb9418965f0ae49e911293a98c4cfe092e3870e0f7dfe883d001f`
- Durability result fingerprint: `3803ba46c2d89dd3de56567f226e601f3683483103eb709ced5ab8678ab04ff6`
- Final authority fingerprint: `6dcc8c5217df016e20e69c2b66cd7fdb9976cd046f02ec85f28d116b56ee042a`

## Passed foundation evidence

- 20 persisted Sessions, 20 database close/reopen cycles, 20 distinct episode IDs,
  and 20 distinct frozen event-window hashes;
- 3,000 minutes of equivalent table-state scale;
- 20 idempotent Session End snapshots and 19 Continue transitions;
- 140 authoritative events, 60 source-bound memories, two campaign objectives,
  and 22 objective-history events;
- an actual persisted model-generation switch in Session 10 from version 1 to
  version 2, with an unchanged game-authority fingerprint and successful recovery
  after the next process restart;
- player memory probes before and after FTS5 rebuild;
- zero KP-only markers in player continuity projections;
- 50 independently identified edge-case regressions passed in 50.79 seconds.

The evidence run also caught and forced repair of a same-second continuity bug:
timestamp-plus-random-ID ordering could select an older snapshot during rapid
End/Continue cycles. Commit `82a7597` changed latest-snapshot selection to durable
insertion order and added both a forced-identical-timestamp regression and the
20-distinct-window gate before this evidence was regenerated.

## Fail-closed gate result

The release gate remained `ready=false`. Remaining blockers are:

- three complete repair/retest rounds;
- all eight required player/GM persona journeys;
- player-observable evidence for `RPS-01..12`;
- a setup-to-Continue real player UI journey;
- a four-player Full AI journey.

No status in this file supersedes the capability catalogue. It records only the
inputs and outcome of this run.
