"""Migration 47: preserve system-neutral random evidence beside legacy check data."""

from __future__ import annotations

import json
import sqlite3
from hashlib import sha256
from typing import Any

VERSION = 47
NAME = "add_check_random_evidence"


def _evidence(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    ones = raw.get("ones_digit")
    tens = raw.get("tens_digits")
    if not isinstance(ones, int) or not isinstance(tens, list) or not tens:
        return None
    if not 0 <= ones <= 9 or any(
        not isinstance(value, int) or not 0 <= value <= 9 for value in tens
    ):
        return None
    request = {
        "schema_version": "dice-roll.v1",
        "components": [
            {"key": "ones_digit", "count": 1, "sides": 10, "minimum": 0},
            {
                "key": "tens_digits",
                "count": len(tens),
                "sides": 10,
                "minimum": 0,
            },
        ],
    }
    rolls = {"ones_digit": [ones], "tens_digits": tens}
    fingerprint_payload = {"request": request, "rolls": rolls}
    fingerprint = sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        **request,
        "rolls": rolls,
        "evidence_fingerprint": fingerprint,
    }


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        ALTER TABLE skill_checks
        ADD COLUMN random_evidence_json TEXT
          CHECK (random_evidence_json IS NULL OR json_valid(random_evidence_json))
        """
    )
    rows = connection.execute(
        """
        SELECT id, raw_dice_json
        FROM skill_checks
        WHERE raw_dice_json IS NOT NULL
        """
    ).fetchall()
    for row in rows:
        try:
            evidence = _evidence(json.loads(row["raw_dice_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            evidence = None
        if evidence is not None:
            connection.execute(
                """
                UPDATE skill_checks
                SET random_evidence_json = ?
                WHERE id = ?
                """,
                (
                    json.dumps(evidence, ensure_ascii=False, separators=(",", ":")),
                    row["id"],
                ),
            )
