"""Migration 15: synchronized FTS5 trigram index for durable memories."""

import sqlite3

VERSION = 15
NAME = "add_memory_fts5_index"

CREATE_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
  text,
  scope,
  campaign_id UNINDEXED,
  pc_id UNINDEXED,
  visibility UNINDEXED,
  content='memories',
  content_rowid='rowid',
  tokenize='trigram'
)
"""

CREATE_INSERT_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS trg_memories_fts_insert
AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, text, scope, campaign_id, pc_id, visibility)
  VALUES (new.rowid, new.text, new.scope, new.campaign_id, new.pc_id, new.visibility);
END
"""

CREATE_DELETE_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS trg_memories_fts_delete
AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts(
    memories_fts, rowid, text, scope, campaign_id, pc_id, visibility
  )
  VALUES (
    'delete', old.rowid, old.text, old.scope, old.campaign_id, old.pc_id, old.visibility
  );
END
"""

CREATE_UPDATE_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS trg_memories_fts_update
AFTER UPDATE ON memories BEGIN
  INSERT INTO memories_fts(
    memories_fts, rowid, text, scope, campaign_id, pc_id, visibility
  )
  VALUES (
    'delete', old.rowid, old.text, old.scope, old.campaign_id, old.pc_id, old.visibility
  );
  INSERT INTO memories_fts(rowid, text, scope, campaign_id, pc_id, visibility)
  VALUES (new.rowid, new.text, new.scope, new.campaign_id, new.pc_id, new.visibility);
END
"""


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(CREATE_FTS)
    connection.execute(CREATE_INSERT_TRIGGER)
    connection.execute(CREATE_DELETE_TRIGGER)
    connection.execute(CREATE_UPDATE_TRIGGER)
    connection.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
