# AC-LONG foundation V4 evidence — 2026-09-01

This is immutable, non-UI foundation evidence. It does not claim that AC-LONG has
passed.

## Source and command

- Source commit: `0914ce6b76d4d69896a04a1853ec37a2892be0f5`
- Runner: `long-campaign-durability.v4`
- Evidence JSON: [`AC_LONG_FOUNDATION_V4_2026-09-01.json`](AC_LONG_FOUNDATION_V4_2026-09-01.json)
- JSON SHA-256: `41cc60d4d72275628b5bff8a6f790f9184e7b1c50c7c47f12d11bded4ea8d9ec`
- Command: `PYTHONPATH=src .venv/bin/python scripts/long_campaign_acceptance.py --output docs/evidence/AC_LONG_FOUNDATION_V4_2026-09-01.json`

## Passed evidence

- 20 persistent Sessions and 3,000 equivalent table minutes;
- 20 database close/reopen checkpoints, model generation switch, FTS rebuild,
  model-off replay, stable authority fingerprint, and zero player-projection secret leaks;
- 50 independently catalogued edge cases (`50 passed`);
- two ruleset-driven deaths, two player-confirmed observer/replacement sequences,
  one temporary departure/return across a restart, and one late player joining through
  a stable seat with an approved investigator;
- all investigator, lifecycle request/event, member presence, stable profile, and seat
  state included in the cross-restart authority fingerprint.

## Fail-closed gate result

`ready=false`. The remaining blockers are three complete repair/retest rounds, all
eight persona observations, `RPS-01..12` observations, a real player-UI
setup-to-continue journey, and the four-player Full AI journey. The V4 lifecycle
result closes the previous synthetic-milestone gap but does not replace those
browser/model requirements.
