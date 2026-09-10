# AC-LONG foundation evidence — 2026-08-22

This is immutable evidence for the non-UI foundation run. It is not an
`AC-LONG` pass and must not be presented as a validated long-running product.

## Source and command

- Source commit before the evidence implementation: `bafcf20f98823f148a8884a085233026f0418527`
- Command:

  ```bash
  PYTHONPATH=src .venv/bin/python scripts/long_campaign_acceptance.py \
    --output /tmp/ai-kp-ac-long-foundation-v2.json
  ```

- Full JSON SHA-256: `6642dd5c775c8edcc2582f465fd0deabff910a094197f24e105b41edd05d32b3`
- Edge-test transcript SHA-256: `595728b2179912d09a1f35b3757b89ab6586544f4d27e46f332f1a4b1f29de6a`
- Durability result fingerprint: `bdfa682d8d847d53605af638e7cf46afb42138d3b5cfa662e16abc78ed7781da`
- Final authority fingerprint: `2031cdf4af8f1c0d18480fd71fed74c46cd5e547e8baf4fd79e7978a867376a4`

## Passed evidence

- 20 persisted Sessions and 20 database close/reopen cycles;
- 3,000 minutes of equivalent table-state scale;
- 20 idempotent Session End snapshots and 19 Continue transitions;
- 140 authoritative events and 60 source-bound memories;
- player memory probes before and after FTS5 rebuild;
- KP-only marker absent from every player continuity projection;
- authority fingerprint unchanged after the final restart;
- 50 independently identified edge-case regressions passed;
- average Session cycle 8.164 ms, maximum 9.806 ms on this development machine.

The first ten episode labels cover the required long-arc milestones, but labels
alone are not evidence that each gameplay subsystem was exercised. The real UI
and persona runs must provide that evidence.

## Fail-closed gate result

The release gate correctly remained `ready=false`. Remaining blockers at this
run were:

- three complete repair/retest rounds;
- all eight required player/GM persona journeys;
- player-observable evidence for `RPS-01..12`;
- a setup-to-Continue real UI journey;
- a four-player Full AI journey;
- model-switch recovery.

No status in this file supersedes the capability catalogue. It records only the
inputs and outcome of this run.
