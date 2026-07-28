"""SQLite connection, base schema, migration, and transaction lifecycle."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ai_kp.infrastructure.database.migrations import apply_migrations


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS campaigns (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  system TEXT NOT NULL DEFAULT 'coc7',
  current_time TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS player_profiles (
  id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS investigators (
  id TEXT PRIMARY KEY,
  owner_profile_id TEXT NOT NULL REFERENCES player_profiles(id) ON DELETE CASCADE,
  ruleset_id TEXT NOT NULL DEFAULT 'coc7-keeper-cn-2002c',
  name TEXT NOT NULL,
  current_revision_id TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  archived_at TEXT
);

CREATE TABLE IF NOT EXISTS investigator_revisions (
  id TEXT PRIMARY KEY,
  investigator_id TEXT NOT NULL REFERENCES investigators(id) ON DELETE CASCADE,
  revision_no INTEGER NOT NULL,
  canonical_json TEXT NOT NULL,
  public_summary_json TEXT NOT NULL DEFAULT '{}',
  source_type TEXT NOT NULL CHECK (source_type IN ('manual', 'xlsx')),
  source_hash TEXT,
  template_id TEXT,
  parser_version TEXT,
  warnings_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(investigator_id, revision_no)
);

CREATE TABLE IF NOT EXISTS character_imports (
  id TEXT PRIMARY KEY,
  owner_profile_id TEXT NOT NULL REFERENCES player_profiles(id) ON DELETE CASCADE,
  investigator_id TEXT NOT NULL REFERENCES investigators(id) ON DELETE CASCADE,
  revision_id TEXT NOT NULL REFERENCES investigator_revisions(id) ON DELETE CASCADE,
  source_filename TEXT NOT NULL,
  source_hash TEXT NOT NULL,
  template_id TEXT NOT NULL,
  parser_version TEXT NOT NULL,
  warnings_json TEXT NOT NULL DEFAULT '[]',
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
  revoked_at TEXT,
  player_profile_id TEXT REFERENCES player_profiles(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS player_characters (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  sheet_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS campaign_investigators (
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  investigator_id TEXT NOT NULL REFERENCES investigators(id) ON DELETE CASCADE,
  owner_profile_id TEXT NOT NULL REFERENCES player_profiles(id) ON DELETE CASCADE,
  submitted_revision_id TEXT REFERENCES investigator_revisions(id) ON DELETE RESTRICT,
  approved_revision_id TEXT REFERENCES investigator_revisions(id) ON DELETE RESTRICT,
  legacy_pc_id TEXT REFERENCES player_characters(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'submitted', 'changes_requested', 'approved', 'withdrawn')),
  review_comment TEXT,
  reviewed_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  reviewed_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (campaign_id, investigator_id)
);

CREATE TABLE IF NOT EXISTS character_reviews (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  investigator_id TEXT NOT NULL REFERENCES investigators(id) ON DELETE CASCADE,
  revision_id TEXT NOT NULL REFERENCES investigator_revisions(id) ON DELETE RESTRICT,
  action TEXT NOT NULL
    CHECK (action IN ('submitted', 'changes_requested', 'approved', 'withdrawn')),
  actor_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  actor_profile_id TEXT REFERENCES player_profiles(id) ON DELETE SET NULL,
  comment TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS investigator_campaign_state (
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  investigator_id TEXT NOT NULL REFERENCES investigators(id) ON DELETE CASCADE,
  approved_revision_id TEXT NOT NULL REFERENCES investigator_revisions(id) ON DELETE RESTRICT,
  current_hp INTEGER NOT NULL,
  current_san INTEGER NOT NULL,
  current_mp INTEGER NOT NULL,
  current_luck INTEGER NOT NULL,
  conditions_json TEXT NOT NULL DEFAULT '[]',
  inventory_delta_json TEXT NOT NULL DEFAULT '{}',
  state_version INTEGER NOT NULL DEFAULT 0,
  current_game_time TEXT,
  last_event_id TEXT REFERENCES events(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (campaign_id, investigator_id)
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
  current_revision_id TEXT,
  selected_public_asset_id TEXT,
  reveal_version INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS map_locations (
  id TEXT PRIMARY KEY,
  map_id TEXT NOT NULL REFERENCES maps(id) ON DELETE CASCADE,
  element_id TEXT,
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
  element_id TEXT,
  start_location_id TEXT NOT NULL REFERENCES map_locations(id) ON DELETE CASCADE,
  end_location_id TEXT NOT NULL REFERENCES map_locations(id) ON DELETE CASCADE,
  travel_time TEXT,
  visibility TEXT NOT NULL DEFAULT 'table',
  notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS map_revisions (
  id TEXT PRIMARY KEY,
  map_id TEXT NOT NULL REFERENCES maps(id) ON DELETE CASCADE,
  revision_no INTEGER NOT NULL,
  spec_version TEXT NOT NULL,
  spec_json TEXT NOT NULL,
  spec_hash TEXT NOT NULL,
  layout_hash TEXT NOT NULL,
  validation_json TEXT NOT NULL DEFAULT '{}',
  source_kind TEXT NOT NULL DEFAULT 'kp_brief',
  created_by TEXT NOT NULL DEFAULT 'ai',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(map_id, revision_no),
  UNIQUE(map_id, spec_hash)
);

CREATE TABLE IF NOT EXISTS map_assets (
  id TEXT PRIMARY KEY,
  map_id TEXT NOT NULL REFERENCES maps(id) ON DELETE CASCADE,
  revision_id TEXT NOT NULL REFERENCES map_revisions(id) ON DELETE CASCADE,
  audience TEXT NOT NULL DEFAULT 'table' CHECK (audience IN ('table', 'kp')),
  kind TEXT NOT NULL DEFAULT 'background' CHECK (kind IN ('background', 'thumbnail')),
  status TEXT NOT NULL DEFAULT 'ready' CHECK (status IN ('ready', 'failed')),
  generation_input_hash TEXT NOT NULL,
  content_hash TEXT,
  storage_path TEXT,
  mime_type TEXT,
  width INTEGER,
  height INTEGER,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  seed INTEGER,
  parameters_json TEXT NOT NULL DEFAULT '{}',
  prompt_text TEXT NOT NULL DEFAULT '',
  error_text TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(map_id, generation_input_hash)
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

CREATE TABLE IF NOT EXISTS rule_sources (
  id TEXT PRIMARY KEY,
  ruleset_id TEXT NOT NULL,
  title TEXT NOT NULL,
  source_filename TEXT NOT NULL,
  source_hash TEXT NOT NULL UNIQUE,
  page_count INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'extracted'
    CHECK (status IN ('extracting', 'extracted', 'indexing', 'ready', 'failed')),
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS rule_ingestion_runs (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES rule_sources(id) ON DELETE CASCADE,
  status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
  stage TEXT NOT NULL,
  cursor_page INTEGER NOT NULL DEFAULT 0,
  agent_model TEXT,
  prompt_version TEXT,
  processed_count INTEGER NOT NULL DEFAULT 0,
  accepted_count INTEGER NOT NULL DEFAULT 0,
  rejected_count INTEGER NOT NULL DEFAULT 0,
  error_text TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS rule_chunks (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES rule_sources(id) ON DELETE CASCADE,
  page_start INTEGER NOT NULL,
  page_end INTEGER NOT NULL,
  order_index INTEGER NOT NULL,
  chapter TEXT,
  section TEXT,
  content_kind TEXT NOT NULL DEFAULT 'text' CHECK (content_kind IN ('text', 'table')),
  audience TEXT NOT NULL DEFAULT 'all' CHECK (audience IN ('all', 'player', 'kp')),
  text TEXT NOT NULL,
  text_hash TEXT NOT NULL,
  extraction_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (extraction_status IN ('pending', 'processing', 'completed', 'failed')),
  minirag_doc_id TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(source_id, order_index),
  UNIQUE(source_id, text_hash)
);

CREATE TABLE IF NOT EXISTS rule_objects (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES rule_sources(id) ON DELETE CASCADE,
  rule_key TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  rule_type TEXT NOT NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'candidate'
    CHECK (status IN ('candidate', 'validated', 'review_required', 'quarantined')),
  object_json TEXT NOT NULL,
  object_hash TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0,
  validation_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(source_id, rule_key, version)
);

CREATE TABLE IF NOT EXISTS rule_object_citations (
  rule_object_id TEXT NOT NULL REFERENCES rule_objects(id) ON DELETE CASCADE,
  chunk_id TEXT NOT NULL REFERENCES rule_chunks(id) ON DELETE CASCADE,
  page INTEGER NOT NULL,
  evidence_text TEXT NOT NULL,
  evidence_hash TEXT NOT NULL,
  PRIMARY KEY (rule_object_id, chunk_id, evidence_hash)
);

CREATE TABLE IF NOT EXISTS rule_relations (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES rule_sources(id) ON DELETE CASCADE,
  source_rule_key TEXT NOT NULL,
  target_rule_key TEXT NOT NULL,
  relation_type TEXT NOT NULL,
  evidence_chunk_id TEXT REFERENCES rule_chunks(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(source_id, source_rule_key, target_rule_key, relation_type)
);

CREATE TABLE IF NOT EXISTS rule_validation_issues (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES rule_sources(id) ON DELETE CASCADE,
  chunk_id TEXT REFERENCES rule_chunks(id) ON DELETE SET NULL,
  run_id TEXT REFERENCES rule_ingestion_runs(id) ON DELETE SET NULL,
  validation_layer TEXT NOT NULL,
  error_text TEXT NOT NULL,
  candidate_json TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_events_campaign_created ON events(campaign_id, created_at);
CREATE INDEX IF NOT EXISTS idx_investigators_owner
  ON investigators(owner_profile_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_investigator_revisions_character
  ON investigator_revisions(investigator_id, revision_no);
CREATE INDEX IF NOT EXISTS idx_character_imports_owner
  ON character_imports(owner_profile_id, created_at);
CREATE INDEX IF NOT EXISTS idx_campaign_investigators_status
  ON campaign_investigators(campaign_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_campaign_investigators_owner
  ON campaign_investigators(owner_profile_id, campaign_id);
CREATE INDEX IF NOT EXISTS idx_character_reviews_target
  ON character_reviews(campaign_id, investigator_id, created_at);
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
CREATE INDEX IF NOT EXISTS idx_map_revisions_map ON map_revisions(map_id, revision_no);
CREATE INDEX IF NOT EXISTS idx_map_assets_map ON map_assets(map_id, created_at);
CREATE INDEX IF NOT EXISTS idx_map_tokens_map ON map_tokens(map_id);
CREATE INDEX IF NOT EXISTS idx_map_token_moves_token ON map_token_moves(token_id, created_at);
CREATE INDEX IF NOT EXISTS idx_module_chunks_module_order ON module_chunks(module_id, order_index);
CREATE INDEX IF NOT EXISTS idx_module_chunks_visibility ON module_chunks(visibility);
CREATE INDEX IF NOT EXISTS idx_memories_campaign_scope ON memories(campaign_id, scope);
CREATE INDEX IF NOT EXISTS idx_memories_pc ON memories(pc_id);
CREATE INDEX IF NOT EXISTS idx_memories_npc ON memories(npc_id);
CREATE INDEX IF NOT EXISTS idx_rule_sources_ruleset ON rule_sources(ruleset_id, status);
CREATE INDEX IF NOT EXISTS idx_rule_chunks_source_page
  ON rule_chunks(source_id, page_start, order_index);
CREATE INDEX IF NOT EXISTS idx_rule_chunks_status
  ON rule_chunks(source_id, extraction_status);
CREATE INDEX IF NOT EXISTS idx_rule_objects_key_status
  ON rule_objects(source_id, rule_key, status);
CREATE INDEX IF NOT EXISTS idx_rule_citations_chunk
  ON rule_object_citations(chunk_id);
CREATE INDEX IF NOT EXISTS idx_rule_validation_issues_source
  ON rule_validation_issues(source_id, validation_layer);
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
