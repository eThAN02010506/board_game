import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS campaigns (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  system TEXT NOT NULL DEFAULT 'coc7',
  current_time TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS player_characters (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  sheet_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS npcs (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  home_location TEXT,
  profession TEXT,
  public_notes TEXT NOT NULL DEFAULT '',
  secret_notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS campaign_npcs (
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  npc_id TEXT NOT NULL REFERENCES npcs(id) ON DELETE CASCADE,
  role TEXT NOT NULL DEFAULT 'encountered',
  first_seen_time TEXT,
  last_seen_time TEXT,
  relationship_score INTEGER NOT NULL DEFAULT 0,
  notes TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (campaign_id, npc_id)
);

CREATE TABLE IF NOT EXISTS modules (
  id TEXT PRIMARY KEY,
  campaign_id TEXT REFERENCES campaigns(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  source_type TEXT NOT NULL DEFAULT 'plaintext',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS module_chunks (
  id TEXT PRIMARY KEY,
  module_id TEXT NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  text TEXT NOT NULL,
  visibility TEXT NOT NULL DEFAULT 'kp',
  spoiler_tag TEXT,
  scene_key TEXT,
  order_index INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  actor_type TEXT NOT NULL,
  actor_id TEXT,
  visibility TEXT NOT NULL DEFAULT 'table',
  event_type TEXT NOT NULL,
  happened_at TEXT,
  summary TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY,
  campaign_id TEXT REFERENCES campaigns(id) ON DELETE CASCADE,
  pc_id TEXT REFERENCES player_characters(id) ON DELETE CASCADE,
  npc_id TEXT REFERENCES npcs(id) ON DELETE CASCADE,
  scope TEXT NOT NULL,
  importance INTEGER NOT NULL DEFAULT 1,
  visibility TEXT NOT NULL DEFAULT 'table',
  happened_at TEXT,
  text TEXT NOT NULL,
  source_event_id TEXT REFERENCES events(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_events_campaign_created ON events(campaign_id, created_at);
CREATE INDEX IF NOT EXISTS idx_module_chunks_module_order ON module_chunks(module_id, order_index);
CREATE INDEX IF NOT EXISTS idx_module_chunks_visibility ON module_chunks(visibility);
CREATE INDEX IF NOT EXISTS idx_memories_campaign_scope ON memories(campaign_id, scope);
CREATE INDEX IF NOT EXISTS idx_memories_pc ON memories(pc_id);
CREATE INDEX IF NOT EXISTS idx_memories_npc ON memories(npc_id);
"""


def connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    connection.commit()


@contextmanager
def db_session(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    connection = connect(db_path)
    try:
        init_db(connection)
        yield connection
        connection.commit()
    finally:
        connection.close()
