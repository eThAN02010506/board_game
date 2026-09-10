"""Migration 65: distinguish resumable coverage supplement cycles."""

import sqlite3

VERSION = 65
NAME = "scenario_contract_supplement_cycles"


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        "ALTER TABLE scenario_contract_job_supplements "
        "ADD COLUMN supplement_cycle INTEGER NOT NULL DEFAULT 0 "
        "CHECK (supplement_cycle >= 0)"
    )
    # SQLite cannot replace the existing primary key with ALTER TABLE. A
    # cycle-local uniqueness index preserves the old cycle-0 rows while
    # allowing later cycles to use globally increasing supplement indexes.
    connection.execute(
        "CREATE UNIQUE INDEX idx_scenario_contract_supplements_cycle "
        "ON scenario_contract_job_supplements"
        "(job_id, supplement_cycle, supplement_index)"
    )
