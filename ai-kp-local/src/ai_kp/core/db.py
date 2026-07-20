import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ai_kp.storage.migrations import apply_migrations


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS campaigns (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  system TEXT NOT NULL DEFAULT 'coc7',
  current_time TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS campaign_sessions (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed')),
  join_code_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  ended_at TEXT
);

CREATE TABLE IF NOT EXISTS session_members (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('kp', 'player')),
  display_name TEXT NOT NULL,
  pc_id TEXT REFERENCES player_characters(id) ON DELETE SET NULL,
  token_hash TEXT NOT NULL UNIQUE,
  joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  revoked_at TEXT
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

CREATE TABLE IF NOT EXISTS maps (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  prompt TEXT NOT NULL DEFAULT '',
  style TEXT NOT NULL DEFAULT 'investigation',
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published')),
  width INTEGER NOT NULL DEFAULT 960,
  height INTEGER NOT NULL DEFAULT 640,
  svg_text TEXT NOT NULL,
  created_by TEXT NOT NULL DEFAULT 'ai',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS map_locations (
  id TEXT PRIMARY KEY,
  map_id TEXT NOT NULL REFERENCES maps(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  x REAL NOT NULL,
  y REAL NOT NULL,
  visibility TEXT NOT NULL DEFAULT 'table',
  notes TEXT NOT NULL DEFAULT '',
  order_index INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS map_routes (
  id TEXT PRIMARY KEY,
  map_id TEXT NOT NULL REFERENCES maps(id) ON DELETE CASCADE,
  start_location_id TEXT NOT NULL REFERENCES map_locations(id) ON DELETE CASCADE,
  end_location_id TEXT NOT NULL REFERENCES map_locations(id) ON DELETE CASCADE,
  travel_time TEXT,
  visibility TEXT NOT NULL DEFAULT 'table',
  notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS map_tokens (
  id TEXT PRIMARY KEY,
  map_id TEXT NOT NULL REFERENCES maps(id) ON DELETE CASCADE,
  label TEXT NOT NULL,
  actor_type TEXT NOT NULL DEFAULT 'pc',
  actor_id TEXT,
  location_id TEXT NOT NULL REFERENCES map_locations(id) ON DELETE CASCADE,
  visibility TEXT NOT NULL DEFAULT 'table',
  color TEXT NOT NULL DEFAULT '#b93f2d',
  version INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS map_token_moves (
  id TEXT PRIMARY KEY,
  token_id TEXT NOT NULL REFERENCES map_tokens(id) ON DELETE CASCADE,
  from_location_id TEXT REFERENCES map_locations(id) ON DELETE SET NULL,
  to_location_id TEXT NOT NULL REFERENCES map_locations(id) ON DELETE CASCADE,
  moved_by TEXT NOT NULL DEFAULT 'player',
  note TEXT NOT NULL DEFAULT '',
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

CREATE TABLE IF NOT EXISTS turn_proposals (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  pc_id TEXT REFERENCES player_characters(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'draft',
  player_action TEXT NOT NULL,
  public_narration TEXT NOT NULL,
  kp_notes TEXT NOT NULL DEFAULT '',
  proposed_checks_json TEXT NOT NULL DEFAULT '[]',
  proposed_events_json TEXT NOT NULL DEFAULT '[]',
  proposed_memories_json TEXT NOT NULL DEFAULT '[]',
  proposed_npc_updates_json TEXT NOT NULL DEFAULT '[]',
  proposed_map_moves_json TEXT NOT NULL DEFAULT '[]',
  source_model TEXT NOT NULL DEFAULT 'unknown',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  applied_at TEXT
);

CREATE TABLE IF NOT EXISTS proposal_actions (
  id TEXT PRIMARY KEY,
  proposal_id TEXT NOT NULL REFERENCES turn_proposals(id) ON DELETE CASCADE,
  action_type TEXT NOT NULL,
  actor TEXT NOT NULL DEFAULT 'human_kp',
  note TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS context_assemblies (
  id TEXT PRIMARY KEY,
  proposal_id TEXT NOT NULL UNIQUE REFERENCES turn_proposals(id) ON DELETE CASCADE,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  visibility_scope TEXT NOT NULL DEFAULT 'kp',
  final_prompt_json TEXT NOT NULL,
  included_sources_json TEXT NOT NULL DEFAULT '[]',
  excluded_sources_json TEXT NOT NULL DEFAULT '[]',
  token_estimate INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS player_actions (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE CASCADE,
  pc_id TEXT REFERENCES player_characters(id) ON DELETE SET NULL,
  action_text TEXT NOT NULL,
  location TEXT,
  map_id TEXT REFERENCES maps(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'submitted'
    CHECK (status IN ('submitted', 'reviewed', 'resolved', 'rejected')),
  proposal_id TEXT REFERENCES turn_proposals(id) ON DELETE SET NULL,
  client_action_id TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS realtime_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_key TEXT NOT NULL UNIQUE,
  session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  audience TEXT NOT NULL CHECK (audience IN ('session', 'kp', 'member')),
  member_id TEXT REFERENCES session_members(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  resource_type TEXT,
  resource_id TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (
    (audience = 'member' AND member_id IS NOT NULL)
    OR (audience != 'member' AND member_id IS NULL)
  )
);

CREATE TABLE IF NOT EXISTS realtime_tickets (
  id TEXT PRIMARY KEY,
  ticket_hash TEXT NOT NULL UNIQUE,
  session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE CASCADE,
  expires_at INTEGER NOT NULL,
  consumed_at TEXT,
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
CREATE UNIQUE INDEX IF NOT EXISTS idx_campaign_sessions_one_active
  ON campaign_sessions(campaign_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_session_members_session ON session_members(session_id, joined_at);
CREATE INDEX IF NOT EXISTS idx_session_members_campaign ON session_members(campaign_id, role);
CREATE UNIQUE INDEX IF NOT EXISTS idx_session_members_active_pc
  ON session_members(session_id, pc_id)
  WHERE pc_id IS NOT NULL AND revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_player_actions_session ON player_actions(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_player_actions_campaign_status
  ON player_actions(campaign_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_realtime_events_session_cursor
  ON realtime_events(session_id, id);
CREATE INDEX IF NOT EXISTS idx_realtime_events_member_cursor
  ON realtime_events(member_id, id);
CREATE INDEX IF NOT EXISTS idx_realtime_tickets_expiry
  ON realtime_tickets(expires_at, consumed_at);
CREATE INDEX IF NOT EXISTS idx_turn_proposals_campaign ON turn_proposals(campaign_id, created_at);
CREATE INDEX IF NOT EXISTS idx_proposal_actions_proposal ON proposal_actions(proposal_id, created_at);
CREATE INDEX IF NOT EXISTS idx_context_assemblies_campaign ON context_assemblies(campaign_id, created_at);
CREATE INDEX IF NOT EXISTS idx_maps_campaign ON maps(campaign_id, created_at);
CREATE INDEX IF NOT EXISTS idx_map_locations_map ON map_locations(map_id, order_index);
CREATE INDEX IF NOT EXISTS idx_map_routes_map ON map_routes(map_id);
CREATE INDEX IF NOT EXISTS idx_map_tokens_map ON map_tokens(map_id);
CREATE INDEX IF NOT EXISTS idx_map_token_moves_token ON map_token_moves(token_id, created_at);
CREATE INDEX IF NOT EXISTS idx_module_chunks_module_order ON module_chunks(module_id, order_index);
CREATE INDEX IF NOT EXISTS idx_module_chunks_visibility ON module_chunks(visibility);
CREATE INDEX IF NOT EXISTS idx_memories_campaign_scope ON memories(campaign_id, scope);
CREATE INDEX IF NOT EXISTS idx_memories_pc ON memories(pc_id);
CREATE INDEX IF NOT EXISTS idx_memories_npc ON memories(npc_id);
"""


def connect(db_path: Path | str) -> sqlite3.Connection:
    raw_path = str(db_path)
    is_memory = raw_path == ":memory:"
    sqlite_path: Path | str
    if is_memory:
        sqlite_path = raw_path
    else:
        sqlite_path = Path(db_path)
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(sqlite_path, timeout=5.0, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA foreign_keys = ON")
    if not is_memory:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def init_db(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    apply_migrations(connection)
    connection.commit()


@contextmanager
def db_session(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    connection = connect(db_path)
    try:
        init_db(connection)
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
