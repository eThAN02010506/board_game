"""Allow deterministic rules provenance without rewriting historic receipts."""

import sqlite3

VERSION = 91
NAME = "world_state_rules_source"


def migrate(connection: sqlite3.Connection) -> None:
    table = "campaign_world_entity_state_changes"
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if row is None or "'rules_kernel'" in row[0]:
        return
    old_sql = row[0]
    constraint = "source_kind IN ('human_kp', 'ai_kp')"
    if constraint not in old_sql:
        raise ValueError("Unexpected world entity state provenance constraint")
    # No table references this leaf ledger. Preserve its indexes and triggers,
    # using create/copy/drop/rename rather than editing sqlite_master directly.
    dependents = [
        item[0]
        for item in connection.execute(
            "SELECT sql FROM sqlite_master WHERE tbl_name=? "
            "AND type IN ('index', 'trigger') AND sql IS NOT NULL",
            (table,),
        )
    ]
    connection.execute(
        old_sql.replace(table, table + "_v91", 1).replace(
            constraint, "source_kind IN ('human_kp', 'ai_kp', 'rules_kernel')"
        )
    )
    connection.execute(
        "INSERT INTO campaign_world_entity_state_changes_v91 "
        "SELECT * FROM campaign_world_entity_state_changes"
    )
    connection.execute("DROP TABLE campaign_world_entity_state_changes")
    connection.execute(
        "ALTER TABLE campaign_world_entity_state_changes_v91 "
        "RENAME TO campaign_world_entity_state_changes"
    )
    for statement in dependents:
        connection.execute(statement)
