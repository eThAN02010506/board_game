"""Migration 46: pin every campaign to exact executable ruleset contracts."""

import sqlite3

VERSION = 46
NAME = "pin_campaign_ruleset_contracts"


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        ALTER TABLE campaigns
        ADD COLUMN ruleset_id TEXT NOT NULL DEFAULT 'coc7-keeper-cn-2002c'
        """
    )
    connection.execute(
        """
        ALTER TABLE campaigns
        ADD COLUMN ruleset_version TEXT NOT NULL DEFAULT '2002c'
        """
    )
    connection.execute(
        """
        ALTER TABLE campaigns
        ADD COLUMN character_schema_version TEXT NOT NULL
          DEFAULT 'coc7-investigator-v1'
        """
    )
    connection.execute(
        """
        ALTER TABLE campaigns
        ADD COLUMN event_schema_version TEXT NOT NULL DEFAULT 'coc7-event-v1'
        """
    )
