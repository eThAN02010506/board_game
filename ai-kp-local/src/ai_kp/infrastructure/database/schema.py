"""SQLite connection, base schema, migration, and transaction lifecycle."""

import os
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
  session_zero_required INTEGER NOT NULL DEFAULT 0 CHECK (session_zero_required IN (0, 1)),
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
  origin_type TEXT NOT NULL DEFAULT 'player_edit'
    CHECK (origin_type IN ('player_edit', 'xlsx_import', 'milestone')),
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
  role TEXT NOT NULL CHECK (role IN ('kp', 'player', 'observer')),
  display_name TEXT NOT NULL,
  pc_id TEXT REFERENCES player_characters(id) ON DELETE SET NULL,
  token_hash TEXT NOT NULL UNIQUE,
  joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  private_history_from_sequence INTEGER NOT NULL DEFAULT 1 CHECK (
    private_history_from_sequence >= 1
  ),
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
  timeline_branch_id TEXT
    REFERENCES investigator_timeline_branches(id) ON DELETE RESTRICT,
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

CREATE TABLE IF NOT EXISTS investigator_timeline_branches (
  id TEXT PRIMARY KEY,
  investigator_id TEXT NOT NULL REFERENCES investigators(id) ON DELETE CASCADE,
  label TEXT NOT NULL CHECK (length(trim(label)) BETWEEN 1 AND 120),
  is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
  version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS investigator_campaign_participations (
  id TEXT PRIMARY KEY,
  branch_id TEXT NOT NULL
    REFERENCES investigator_timeline_branches(id) ON DELETE RESTRICT,
  investigator_id TEXT NOT NULL REFERENCES investigators(id) ON DELETE CASCADE,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
  approved_revision_id TEXT NOT NULL
    REFERENCES investigator_revisions(id) ON DELETE RESTRICT,
  legacy_pc_id TEXT REFERENCES player_characters(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'completed')),
  world_started_at TEXT,
  world_ended_at TEXT,
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  ended_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(session_id, investigator_id)
);

CREATE TABLE IF NOT EXISTS investigator_permanent_change_proposals (
  id TEXT PRIMARY KEY,
  investigator_id TEXT NOT NULL REFERENCES investigators(id) ON DELETE CASCADE,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  branch_id TEXT NOT NULL
    REFERENCES investigator_timeline_branches(id) ON DELETE RESTRICT,
  base_revision_id TEXT NOT NULL
    REFERENCES investigator_revisions(id) ON DELETE RESTRICT,
  source_event_id TEXT NOT NULL REFERENCES events(id) ON DELETE RESTRICT,
  kind TEXT NOT NULL CHECK (
    kind IN (
      'major_experience', 'scar', 'relationship', 'spell',
      'characteristic', 'skill'
    )
  ),
  summary TEXT NOT NULL CHECK (length(trim(summary)) BETWEEN 1 AND 1000),
  change_json TEXT NOT NULL CHECK (json_valid(change_json)),
  rationale TEXT NOT NULL CHECK (length(trim(rationale)) BETWEEN 1 AND 2000),
  status TEXT NOT NULL DEFAULT 'proposed'
    CHECK (status IN ('proposed', 'accepted', 'rejected')),
  proposed_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  decided_by_profile_id TEXT REFERENCES player_profiles(id) ON DELETE SET NULL,
  decision_reason TEXT,
  decision_hash TEXT,
  resulting_revision_id TEXT
    REFERENCES investigator_revisions(id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  decided_at TEXT
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
  source_filename TEXT,
  source_hash TEXT,
  source_storage_path TEXT,
  parser_version TEXT,
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
  content_kind TEXT NOT NULL DEFAULT 'text',
  page_start INTEGER,
  page_end INTEGER,
  paragraph_start INTEGER,
  paragraph_end INTEGER,
  source_locator TEXT,
  semantic_kind TEXT NOT NULL DEFAULT 'text',
  classification_confidence REAL NOT NULL DEFAULT 0,
  style_annotations_json TEXT NOT NULL DEFAULT '[]',
  review_flags_json TEXT NOT NULL DEFAULT '[]',
  heading_level INTEGER CHECK (heading_level BETWEEN 1 AND 9),
  section_path_json TEXT NOT NULL DEFAULT '[]',
  knowledge_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (knowledge_status IN ('pending', 'processing', 'completed', 'failed')),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS module_import_jobs (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  source_filename TEXT NOT NULL,
  source_type TEXT NOT NULL CHECK (source_type IN ('pdf', 'docx')),
  source_hash TEXT NOT NULL,
  source_storage_path TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'processing', 'completed', 'failed')),
  stage TEXT NOT NULL DEFAULT 'queued',
  progress_current INTEGER NOT NULL DEFAULT 0,
  progress_total INTEGER NOT NULL DEFAULT 0,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  error_text TEXT,
  module_id TEXT REFERENCES modules(id) ON DELETE SET NULL,
  parser_version TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS campaign_module_runs (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  module_id TEXT NOT NULL REFERENCES modules(id) ON DELETE RESTRICT,
  module_source_hash TEXT,
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'paused', 'completed')),
  current_scene_key TEXT,
  current_scene_title TEXT,
  play_pace TEXT NOT NULL DEFAULT 'freeform'
    CHECK (play_pace IN ('freeform', 'structured', 'downtime')),
  current_location_entity_id TEXT REFERENCES module_entities(id) ON DELETE SET NULL,
  scene_started_world_time TEXT,
  active_spoiler_tags_json TEXT NOT NULL DEFAULT '[]',
  state_json TEXT NOT NULL DEFAULT '{}',
  version INTEGER NOT NULL DEFAULT 0,
  automation_level TEXT NOT NULL DEFAULT 'conservative'
    CHECK (automation_level IN ('conservative', 'balanced', 'ai_kp')),
  automation_reason TEXT,
  started_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS module_assets (
  id TEXT PRIMARY KEY,
  module_id TEXT NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
  content_hash TEXT NOT NULL,
  storage_path TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  width INTEGER,
  height INTEGER,
  source_locator TEXT NOT NULL,
  nearby_heading TEXT,
  asset_role TEXT NOT NULL DEFAULT 'unknown',
  classification_confidence REAL NOT NULL DEFAULT 0,
  review_flags_json TEXT NOT NULL DEFAULT '[]',
  visibility TEXT NOT NULL DEFAULT 'kp'
    CHECK (visibility IN ('player', 'table', 'kp', 'secret')),
  spoiler_tag TEXT,
  analysis_status TEXT NOT NULL DEFAULT 'pending_analysis'
    CHECK (analysis_status IN ('pending_analysis', 'completed', 'failed')),
  ocr_text TEXT,
  visual_summary TEXT,
  analysis_model TEXT,
  analysis_error TEXT,
  analysis_prompt_version TEXT,
  analyzed_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS module_knowledge_candidates (
  id TEXT PRIMARY KEY,
  module_id TEXT NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('module_canon', 'module_anchor', 'reference')),
  title TEXT NOT NULL,
  statement TEXT NOT NULL,
  rationale TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL DEFAULT 0 CHECK (confidence BETWEEN 0 AND 1),
  visibility TEXT NOT NULL DEFAULT 'kp'
    CHECK (visibility IN ('player', 'table', 'kp', 'secret')),
  spoiler_tag TEXT,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'approved', 'rejected')),
  object_hash TEXT NOT NULL,
  created_by TEXT NOT NULL DEFAULT 'ai' CHECK (created_by IN ('ai', 'human_kp')),
  source_model TEXT,
  prompt_version TEXT,
  review_note TEXT,
  reviewed_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  reviewed_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  entity_name TEXT,
  entity_type TEXT,
  UNIQUE(module_id, object_hash)
);

CREATE TABLE IF NOT EXISTS module_knowledge_citations (
  candidate_id TEXT NOT NULL
    REFERENCES module_knowledge_candidates(id) ON DELETE CASCADE,
  chunk_id TEXT REFERENCES module_chunks(id) ON DELETE CASCADE,
  asset_id TEXT REFERENCES module_assets(id) ON DELETE CASCADE,
  evidence_text TEXT NOT NULL,
  evidence_hash TEXT NOT NULL,
  source_locator TEXT NOT NULL,
  PRIMARY KEY (candidate_id, evidence_hash),
  CHECK (
    (chunk_id IS NOT NULL AND asset_id IS NULL)
    OR (chunk_id IS NULL AND asset_id IS NOT NULL)
  )
);

CREATE TABLE IF NOT EXISTS module_entities (
  id TEXT PRIMARY KEY,
  module_id TEXT NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
  entity_type TEXT NOT NULL CHECK (
    entity_type IN ('npc', 'location', 'clue', 'organization', 'item', 'event', 'anchor')
  ),
  name TEXT NOT NULL,
  normalized_name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  visibility TEXT NOT NULL DEFAULT 'kp'
    CHECK (visibility IN ('player', 'table', 'kp', 'secret')),
  spoiler_tag TEXT,
  source_candidate_id TEXT NOT NULL
    REFERENCES module_knowledge_candidates(id) ON DELETE RESTRICT,
  created_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(module_id, entity_type, normalized_name)
);

CREATE TABLE IF NOT EXISTS module_entity_candidates (
  id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES module_entities(id) ON DELETE CASCADE,
  candidate_id TEXT NOT NULL
    REFERENCES module_knowledge_candidates(id) ON DELETE RESTRICT,
  role TEXT NOT NULL DEFAULT 'source'
    CHECK (role IN ('source', 'reference')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(entity_id, candidate_id)
);

CREATE TABLE IF NOT EXISTS module_entity_relations (
  id TEXT PRIMARY KEY,
  module_id TEXT NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
  source_entity_id TEXT NOT NULL REFERENCES module_entities(id) ON DELETE CASCADE,
  predicate TEXT NOT NULL CHECK (
    predicate IN (
      'contains', 'located_at', 'knows', 'owns', 'member_of', 'reveals',
      'leads_to', 'provides_access_to', 'blocks', 'contradicts', 'same_as', 'involves'
    )
  ),
  target_entity_id TEXT NOT NULL REFERENCES module_entities(id) ON DELETE CASCADE,
  source_candidate_id TEXT NOT NULL
    REFERENCES module_knowledge_candidates(id) ON DELETE RESTRICT,
  confidence REAL NOT NULL DEFAULT 1 CHECK (confidence BETWEEN 0 AND 1),
  visibility TEXT NOT NULL DEFAULT 'kp'
    CHECK (visibility IN ('player', 'table', 'kp', 'secret')),
  spoiler_tag TEXT,
  note TEXT NOT NULL DEFAULT '',
  created_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (source_entity_id <> target_entity_id),
  UNIQUE(module_id, source_entity_id, predicate, target_entity_id)
);

CREATE TABLE IF NOT EXISTS module_setting_profiles (
  id TEXT PRIMARY KEY,
  module_id TEXT NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
  title TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 200),
  setting_pack_id TEXT NOT NULL CHECK (length(trim(setting_pack_id)) BETWEEN 1 AND 80),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
  current_version INTEGER NOT NULL DEFAULT 1 CHECK (current_version > 0),
  created_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  updated_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(module_id, title)
);

CREATE TABLE IF NOT EXISTS module_setting_profile_versions (
  profile_id TEXT NOT NULL REFERENCES module_setting_profiles(id) ON DELETE CASCADE,
  version INTEGER NOT NULL CHECK (version > 0),
  setting_pack_version TEXT NOT NULL
    CHECK (length(trim(setting_pack_version)) BETWEEN 1 AND 40),
  document_json TEXT NOT NULL
    CHECK (json_valid(document_json) AND json_type(document_json) = 'object'),
  content_hash TEXT NOT NULL CHECK (
    length(content_hash) = 64 AND content_hash NOT GLOB '*[^0-9a-f]*'
  ),
  created_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (profile_id, version)
);

CREATE TABLE IF NOT EXISTS module_run_setting_selections (
  run_id TEXT PRIMARY KEY REFERENCES campaign_module_runs(id) ON DELETE CASCADE,
  profile_id TEXT NOT NULL REFERENCES module_setting_profiles(id) ON DELETE RESTRICT,
  profile_version INTEGER NOT NULL CHECK (profile_version > 0),
  settlement_id TEXT NOT NULL CHECK (length(trim(settlement_id)) BETWEEN 1 AND 160),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  updated_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (profile_id, profile_version)
    REFERENCES module_setting_profile_versions(profile_id, version) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS module_run_setting_selection_events (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL UNIQUE,
  run_id TEXT NOT NULL REFERENCES campaign_module_runs(id) ON DELETE CASCADE,
  from_profile_id TEXT REFERENCES module_setting_profiles(id) ON DELETE SET NULL,
  from_profile_version INTEGER,
  from_settlement_id TEXT,
  to_profile_id TEXT NOT NULL REFERENCES module_setting_profiles(id) ON DELETE RESTRICT,
  to_profile_version INTEGER NOT NULL CHECK (to_profile_version > 0),
  to_settlement_id TEXT NOT NULL,
  changed_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  reason TEXT NOT NULL CHECK (length(trim(reason)) BETWEEN 1 AND 2000),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_module_setting_profiles_module
  ON module_setting_profiles(module_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_module_run_setting_selection_events_run
  ON module_run_setting_selection_events(run_id, sequence DESC);

CREATE TABLE IF NOT EXISTS module_run_scene_events (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES campaign_module_runs(id) ON DELETE CASCADE,
  from_scene_key TEXT,
  to_scene_key TEXT NOT NULL,
  from_scene_title TEXT,
  to_scene_title TEXT NOT NULL,
  from_play_pace TEXT,
  to_play_pace TEXT NOT NULL
    CHECK (to_play_pace IN ('freeform', 'structured', 'downtime')),
  from_location_entity_id TEXT REFERENCES module_entities(id) ON DELETE SET NULL,
  to_location_entity_id TEXT REFERENCES module_entities(id) ON DELETE SET NULL,
  world_time TEXT,
  note TEXT NOT NULL DEFAULT '',
  changed_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS module_run_entity_states (
  run_id TEXT NOT NULL REFERENCES campaign_module_runs(id) ON DELETE CASCADE,
  entity_id TEXT NOT NULL REFERENCES module_entities(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'hidden'
    CHECK (status IN ('hidden', 'available', 'discovered', 'resolved')),
  version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
  updated_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (run_id, entity_id)
);

CREATE TABLE IF NOT EXISTS module_run_entity_state_events (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES campaign_module_runs(id) ON DELETE CASCADE,
  entity_id TEXT NOT NULL REFERENCES module_entities(id) ON DELETE CASCADE,
  from_status TEXT NOT NULL
    CHECK (from_status IN ('hidden', 'available', 'discovered', 'resolved')),
  to_status TEXT NOT NULL
    CHECK (to_status IN ('hidden', 'available', 'discovered', 'resolved')),
  note TEXT NOT NULL DEFAULT '',
  changed_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
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
  proposed_facts_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(proposed_facts_json)),
  proposed_world_entity_states_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(proposed_world_entity_states_json)),
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

CREATE TABLE IF NOT EXISTS world_expansion_materializations (
  id TEXT PRIMARY KEY,
  proposal_id TEXT NOT NULL UNIQUE
    REFERENCES turn_proposals(id) ON DELETE CASCADE,
  campaign_id TEXT NOT NULL
    REFERENCES campaigns(id) ON DELETE CASCADE,
  idempotency_key TEXT NOT NULL,
  command_hash TEXT NOT NULL CHECK (length(command_hash) = 64),
  encounter_event_id TEXT NOT NULL UNIQUE
    REFERENCES events(id) ON DELETE CASCADE,
  npc_id TEXT REFERENCES npcs(id) ON DELETE SET NULL,
  map_token_id TEXT REFERENCES map_tokens(id) ON DELETE SET NULL,
  created_by_member_id TEXT
    REFERENCES session_members(id) ON DELETE SET NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(campaign_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS world_expansion_materialization_facts (
  materialization_id TEXT NOT NULL
    REFERENCES world_expansion_materializations(id) ON DELETE CASCADE,
  fact_event_id TEXT NOT NULL UNIQUE
    REFERENCES events(id) ON DELETE CASCADE,
  order_index INTEGER NOT NULL,
  PRIMARY KEY (materialization_id, fact_event_id),
  UNIQUE(materialization_id, order_index)
);

CREATE TABLE IF NOT EXISTS campaign_world_entities (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  entity_kind TEXT NOT NULL CHECK (entity_kind IN (
    'npc', 'organization', 'item', 'document', 'vehicle', 'event', 'clue_carrier'
  )),
  archetype_id TEXT,
  name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 240),
  description TEXT NOT NULL DEFAULT '' CHECK (length(description) <= 4000),
  visibility TEXT NOT NULL DEFAULT 'table'
    CHECK (visibility IN ('table', 'kp', 'secret')),
  origin_kind TEXT NOT NULL CHECK (origin_kind IN ('module_source', 'world_expansion')),
  origin_ref TEXT NOT NULL CHECK (length(trim(origin_ref)) BETWEEN 1 AND 240),
  npc_id TEXT REFERENCES npcs(id) ON DELETE SET NULL,
  created_from_event_id TEXT NOT NULL REFERENCES events(id) ON DELETE RESTRICT,
  data_json TEXT NOT NULL DEFAULT '{}'
    CHECK (json_valid(data_json) AND json_type(data_json) = 'object'),
  state_version INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(campaign_id, origin_kind, origin_ref)
);

CREATE TABLE IF NOT EXISTS campaign_world_entity_relations (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  source_entity_id TEXT NOT NULL
    REFERENCES campaign_world_entities(id) ON DELETE CASCADE,
  relation_slot_id TEXT NOT NULL CHECK (length(trim(relation_slot_id)) BETWEEN 1 AND 64),
  target_entity_id TEXT NOT NULL
    REFERENCES campaign_world_entities(id) ON DELETE CASCADE,
  created_from_event_id TEXT NOT NULL REFERENCES events(id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (source_entity_id <> target_entity_id),
  UNIQUE(campaign_id, source_entity_id, relation_slot_id, target_entity_id)
);

CREATE TABLE IF NOT EXISTS campaign_world_entity_states (
  entity_id TEXT NOT NULL REFERENCES campaign_world_entities(id) ON DELETE CASCADE,
  dimension TEXT NOT NULL CHECK (
    length(dimension) BETWEEN 1 AND 64
    AND dimension NOT GLOB '*[^a-z0-9_]*'
  ),
  value_json TEXT NOT NULL CHECK (json_valid(value_json)),
  visibility TEXT NOT NULL CHECK (visibility IN ('table', 'kp', 'secret')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  source_event_id TEXT NOT NULL REFERENCES events(id) ON DELETE RESTRICT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (entity_id, dimension)
);

CREATE TABLE IF NOT EXISTS campaign_world_entity_state_changes (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  entity_id TEXT NOT NULL REFERENCES campaign_world_entities(id) ON DELETE CASCADE,
  dimension TEXT NOT NULL CHECK (length(dimension) BETWEEN 1 AND 64),
  from_value_json TEXT CHECK (from_value_json IS NULL OR json_valid(from_value_json)),
  to_value_json TEXT CHECK (to_value_json IS NULL OR json_valid(to_value_json)),
  visibility TEXT NOT NULL CHECK (visibility IN ('table', 'kp', 'secret')),
  note TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 2000),
  source_kind TEXT NOT NULL CHECK (source_kind IN ('human_kp', 'ai_kp', 'rules_kernel')),
  state_version INTEGER NOT NULL CHECK (state_version >= 1),
  idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 200),
  command_hash TEXT NOT NULL CHECK (length(command_hash) = 64),
  event_id TEXT NOT NULL UNIQUE REFERENCES events(id) ON DELETE RESTRICT,
  changed_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(campaign_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS world_expansion_materialization_entities (
  materialization_id TEXT NOT NULL
    REFERENCES world_expansion_materializations(id) ON DELETE CASCADE,
  world_entity_id TEXT NOT NULL
    REFERENCES campaign_world_entities(id) ON DELETE RESTRICT,
  role TEXT NOT NULL CHECK (role IN ('source', 'generated', 'relation_target')),
  local_ref TEXT,
  order_index INTEGER NOT NULL,
  PRIMARY KEY (materialization_id, world_entity_id),
  UNIQUE(materialization_id, order_index)
);

CREATE TABLE IF NOT EXISTS investigator_npc_encounters (
  id TEXT PRIMARY KEY,
  investigator_id TEXT NOT NULL
    REFERENCES investigators(id) ON DELETE CASCADE,
  npc_id TEXT NOT NULL
    REFERENCES npcs(id) ON DELETE CASCADE,
  campaign_id TEXT NOT NULL
    REFERENCES campaigns(id) ON DELETE CASCADE,
  source_event_id TEXT NOT NULL
    REFERENCES events(id) ON DELETE CASCADE,
  materialization_id TEXT
    REFERENCES world_expansion_materializations(id) ON DELETE SET NULL,
  interaction_summary TEXT NOT NULL
    CHECK (length(trim(interaction_summary)) BETWEEN 1 AND 1000),
  happened_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(investigator_id, npc_id, source_event_id)
);

CREATE TABLE IF NOT EXISTS npc_availability_profiles (
  npc_id TEXT PRIMARY KEY REFERENCES npcs(id) ON DELETE CASCADE,
  lifecycle_state TEXT NOT NULL DEFAULT 'unknown'
    CHECK (lifecycle_state IN ('unknown', 'active', 'missing', 'unavailable')),
  born_year INTEGER CHECK (born_year IS NULL OR born_year BETWEEN 1 AND 9999),
  died_year INTEGER CHECK (died_year IS NULL OR died_year BETWEEN 1 AND 9999),
  active_from_year INTEGER
    CHECK (active_from_year IS NULL OR active_from_year BETWEEN 1 AND 9999),
  active_until_year INTEGER
    CHECK (active_until_year IS NULL OR active_until_year BETWEEN 1 AND 9999),
  location_tags_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(location_tags_json)),
  profession_tags_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(profession_tags_json)),
  kp_notes TEXT NOT NULL DEFAULT '' CHECK (length(kp_notes) <= 2000),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (died_year IS NULL OR born_year IS NULL OR died_year >= born_year),
  CHECK (
    active_until_year IS NULL OR active_from_year IS NULL
    OR active_until_year >= active_from_year
  )
);

CREATE TABLE IF NOT EXISTS campaign_npc_reappearance_policies (
  campaign_id TEXT PRIMARY KEY REFERENCES campaigns(id) ON DELETE CASCADE,
  max_returning_npcs INTEGER NOT NULL DEFAULT 1
    CHECK (max_returning_npcs BETWEEN 0 AND 50),
  require_location_match INTEGER NOT NULL DEFAULT 0
    CHECK (require_location_match IN (0, 1)),
  require_profession_match INTEGER NOT NULL DEFAULT 0
    CHECK (require_profession_match IN (0, 1)),
  max_travel_minutes INTEGER NOT NULL DEFAULT 1440
    CHECK (max_travel_minutes BETWEEN 0 AND 525600),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS npc_reappearance_appearances (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  npc_id TEXT NOT NULL REFERENCES npcs(id) ON DELETE CASCADE,
  materialization_id TEXT NOT NULL UNIQUE
    REFERENCES world_expansion_materializations(id) ON DELETE CASCADE,
  appeared_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(campaign_id, npc_id)
);

CREATE TABLE IF NOT EXISTS campaign_travel_locations (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 200),
  normalized_name TEXT NOT NULL CHECK (length(normalized_name) BETWEEN 1 AND 200),
  aliases_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(aliases_json)),
  source_kind TEXT NOT NULL DEFAULT 'manual'
    CHECK (source_kind IN ('manual', 'map', 'module')),
  source_ref TEXT,
  kp_notes TEXT NOT NULL DEFAULT '' CHECK (length(kp_notes) <= 2000),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(campaign_id, normalized_name)
);

CREATE TABLE IF NOT EXISTS campaign_travel_routes (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  from_location_id TEXT NOT NULL
    REFERENCES campaign_travel_locations(id) ON DELETE CASCADE,
  to_location_id TEXT NOT NULL
    REFERENCES campaign_travel_locations(id) ON DELETE CASCADE,
  travel_minutes INTEGER NOT NULL CHECK (travel_minutes BETWEEN 1 AND 525600),
  travel_mode TEXT NOT NULL DEFAULT 'other'
    CHECK (travel_mode IN ('walk', 'drive', 'rail', 'boat', 'flight', 'other')),
  bidirectional INTEGER NOT NULL DEFAULT 1 CHECK (bidirectional IN (0, 1)),
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'blocked')),
  kp_notes TEXT NOT NULL DEFAULT '' CHECK (length(kp_notes) <= 2000),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (from_location_id != to_location_id),
  UNIQUE(campaign_id, from_location_id, to_location_id, travel_mode)
);

CREATE TABLE IF NOT EXISTS npc_hidden_appearance_resolutions (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  npc_id TEXT NOT NULL REFERENCES npcs(id) ON DELETE CASCADE,
  idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 200),
  command_hash TEXT NOT NULL CHECK (length(command_hash) = 64),
  trigger_text TEXT NOT NULL CHECK (length(trim(trigger_text)) BETWEEN 1 AND 500),
  appearance_chance INTEGER NOT NULL CHECK (appearance_chance BETWEEN 0 AND 100),
  appearance_roll INTEGER NOT NULL CHECK (appearance_roll BETWEEN 1 AND 100),
  appears INTEGER NOT NULL CHECK (appears IN (0, 1)),
  eligible_locations_json TEXT NOT NULL CHECK (json_valid(eligible_locations_json)),
  selected_location_id TEXT
    REFERENCES campaign_travel_locations(id) ON DELETE SET NULL,
  selected_location_name TEXT,
  location_roll INTEGER CHECK (location_roll IS NULL OR location_roll >= 1),
  created_by_member_id TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(campaign_id, idempotency_key)
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

CREATE TABLE IF NOT EXISTS auto_kp_jobs (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  run_id TEXT REFERENCES campaign_module_runs(id) ON DELETE SET NULL,
  job_type TEXT NOT NULL
    CHECK (job_type IN ('player_action', 'parallel_actions', 'world_expansion', 'check_consequence', 'encounter_turn')),
  resource_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'running', 'retry_wait', 'succeeded', 'failed', 'needs_attention', 'cancelled')),
  stage TEXT NOT NULL DEFAULT 'queued',
  attempt_count INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  next_run_at TEXT,
  locked_by TEXT,
  locked_at TEXT,
  last_error TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  result_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(campaign_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS player_action_adjudications (
  id TEXT PRIMARY KEY,
  action_id TEXT NOT NULL UNIQUE REFERENCES player_actions(id) ON DELETE CASCADE,
  proposal_id TEXT NOT NULL REFERENCES turn_proposals(id) ON DELETE CASCADE,
  mode TEXT NOT NULL CHECK (mode IN ('direct_resolution', 'skill_check', 'roleplay_or_clarification')),
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'confirmed', 'superseded')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  reason TEXT NOT NULL,
  prompt TEXT NOT NULL DEFAULT '',
  skill_options_json TEXT NOT NULL DEFAULT '[]',
  selected_skill TEXT,
  source_model TEXT NOT NULL DEFAULT 'unknown',
  source_error TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  confirmed_at TEXT
);

CREATE TABLE IF NOT EXISTS player_action_adjudication_events (
  id TEXT PRIMARY KEY,
  adjudication_id TEXT NOT NULL REFERENCES player_action_adjudications(id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  event_type TEXT NOT NULL CHECK (event_type IN ('created', 'skill_changed', 'confirmed', 'superseded')),
  actor_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS action_resolution_previews (
  id TEXT PRIMARY KEY,
  action_id TEXT NOT NULL REFERENCES player_actions(id) ON DELETE CASCADE,
  run_id TEXT REFERENCES campaign_module_runs(id) ON DELETE SET NULL,
  source TEXT NOT NULL
    CHECK (source IN ('legacy_projection', 'kernel_shadow', 'kernel_authority')),
  contract_id TEXT NOT NULL,
  scenario_version INTEGER NOT NULL CHECK (scenario_version > 0),
  snapshot_version INTEGER NOT NULL CHECK (snapshot_version >= 0),
  operator_id TEXT NOT NULL,
  preview_hash TEXT NOT NULL CHECK (length(preview_hash) = 64),
  preview_json TEXT NOT NULL CHECK (json_valid(preview_json)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(action_id, source, snapshot_version, preview_hash)
);
CREATE INDEX IF NOT EXISTS idx_action_resolution_previews_action
  ON action_resolution_previews(action_id, created_at);
CREATE INDEX IF NOT EXISTS idx_action_resolution_previews_run
  ON action_resolution_previews(run_id, created_at);

CREATE TABLE IF NOT EXISTS scenario_contract_versions (
  id TEXT PRIMARY KEY,
  module_id TEXT NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
  contract_key TEXT NOT NULL,
  version INTEGER NOT NULL CHECK (version > 0),
  schema_version INTEGER NOT NULL CHECK (schema_version > 0),
  source_version INTEGER NOT NULL CHECK (source_version > 0),
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'published', 'superseded')),
  row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version > 0),
  contract_hash TEXT NOT NULL CHECK (length(contract_hash) = 64),
  contract_json TEXT NOT NULL CHECK (json_valid(contract_json)),
  validation_json TEXT NOT NULL CHECK (json_valid(validation_json)),
  created_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  published_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  published_at TEXT,
  UNIQUE(module_id, version),
  UNIQUE(module_id, contract_hash)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_scenario_contract_one_published
  ON scenario_contract_versions(module_id) WHERE status = 'published';
CREATE INDEX IF NOT EXISTS idx_scenario_contract_module_version
  ON scenario_contract_versions(module_id, version DESC);
CREATE TABLE IF NOT EXISTS module_run_contract_bindings (
  run_id TEXT PRIMARY KEY REFERENCES campaign_module_runs(id) ON DELETE CASCADE,
  contract_version_id TEXT NOT NULL
    REFERENCES scenario_contract_versions(id) ON DELETE RESTRICT,
  bound_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_module_run_contract_version
  ON module_run_contract_bindings(contract_version_id);

CREATE TABLE IF NOT EXISTS scenario_run_states (
  run_id TEXT PRIMARY KEY REFERENCES campaign_module_runs(id) ON DELETE CASCADE,
  contract_version_id TEXT NOT NULL
    REFERENCES scenario_contract_versions(id) ON DELETE RESTRICT,
  state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0),
  snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS scenario_command_batches (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES scenario_run_states(run_id) ON DELETE CASCADE,
  batch_kind TEXT NOT NULL
    CHECK (batch_kind IN ('action', 'parallel', 'reactive', 'plan_step', 'world_expansion')),
  idempotency_key TEXT NOT NULL,
  expected_version INTEGER NOT NULL CHECK (expected_version >= 0),
  result_version INTEGER NOT NULL CHECK (result_version = expected_version + 1),
  preview_hash TEXT NOT NULL CHECK (length(preview_hash) = 64),
  preview_json TEXT NOT NULL CHECK (json_valid(preview_json)),
  authority_basis_json TEXT
    CHECK (
      authority_basis_json IS NULL
      OR (json_valid(authority_basis_json) AND json_type(authority_basis_json) = 'object')
    ),
  commands_json TEXT NOT NULL CHECK (json_valid(commands_json)),
  snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(run_id, idempotency_key),
  UNIQUE(run_id, result_version)
);
CREATE INDEX IF NOT EXISTS idx_scenario_command_batches_run
  ON scenario_command_batches(run_id, result_version);

CREATE TABLE IF NOT EXISTS parallel_action_batches (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
  run_id TEXT NOT NULL REFERENCES campaign_module_runs(id) ON DELETE CASCADE,
  module_run_version INTEGER NOT NULL CHECK (module_run_version >= 0),
  contract_version_id TEXT NOT NULL
    REFERENCES scenario_contract_versions(id) ON DELETE RESTRICT,
  base_state_version INTEGER NOT NULL CHECK (base_state_version >= 0),
  idempotency_key TEXT NOT NULL
    CHECK (length(trim(idempotency_key)) BETWEEN 8 AND 200),
  action_set_hash TEXT NOT NULL CHECK (length(action_set_hash) = 64),
  preparation_hash TEXT NOT NULL CHECK (length(preparation_hash) = 64),
  status TEXT NOT NULL DEFAULT 'awaiting_confirmation' CHECK (
    status IN (
      'awaiting_confirmation', 'awaiting_checks', 'ready', 'committing',
      'settled', 'needs_attention', 'superseded'
    )
  ),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  attention_reason TEXT NOT NULL DEFAULT '' CHECK (length(attention_reason) <= 2000),
  settlement_hash TEXT CHECK (
    settlement_hash IS NULL OR length(settlement_hash) = 64
  ),
  scenario_command_batch_id TEXT
    REFERENCES scenario_command_batches(id) ON DELETE RESTRICT,
  created_by_member_id TEXT
    REFERENCES session_members(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  settled_at TEXT,
  UNIQUE(campaign_id, idempotency_key),
  UNIQUE(scenario_command_batch_id),
  CHECK (
    (status = 'settled' AND settlement_hash IS NOT NULL
      AND scenario_command_batch_id IS NOT NULL AND settled_at IS NOT NULL)
    OR
    (status != 'settled' AND settlement_hash IS NULL
      AND scenario_command_batch_id IS NULL AND settled_at IS NULL)
  )
);
CREATE INDEX IF NOT EXISTS idx_parallel_action_batches_run_status
  ON parallel_action_batches(run_id, status, created_at, id);
CREATE INDEX IF NOT EXISTS idx_parallel_action_batches_recovery
  ON parallel_action_batches(campaign_id, session_id, status, updated_at, id);

CREATE TABLE IF NOT EXISTS parallel_action_batch_items (
  batch_id TEXT NOT NULL
    REFERENCES parallel_action_batches(id) ON DELETE CASCADE,
  action_id TEXT NOT NULL UNIQUE
    REFERENCES player_actions(id) ON DELETE RESTRICT,
  proposal_id TEXT NOT NULL UNIQUE
    REFERENCES turn_proposals(id) ON DELETE RESTRICT,
  adjudication_id TEXT NOT NULL UNIQUE
    REFERENCES player_action_adjudications(id) ON DELETE RESTRICT,
  actor_id TEXT NOT NULL CHECK (length(trim(actor_id)) BETWEEN 1 AND 160),
  operator_id TEXT NOT NULL CHECK (length(trim(operator_id)) BETWEEN 1 AND 160),
  preview_hash TEXT NOT NULL CHECK (length(preview_hash) = 64),
  selected_skill_key TEXT CHECK (
    selected_skill_key IS NULL OR length(trim(selected_skill_key)) BETWEEN 1 AND 120
  ),
  outcome_key TEXT CHECK (
    outcome_key IS NULL OR length(trim(outcome_key)) BETWEEN 1 AND 120
  ),
  check_result_fingerprint TEXT CHECK (
    check_result_fingerprint IS NULL OR length(check_result_fingerprint) = 64
  ),
  priority INTEGER NOT NULL DEFAULT 0 CHECK (priority BETWEEN -1000 AND 1000),
  PRIMARY KEY (batch_id, action_id),
  UNIQUE(batch_id, proposal_id),
  UNIQUE(batch_id, adjudication_id),
  UNIQUE(batch_id, actor_id)
);
CREATE INDEX IF NOT EXISTS idx_parallel_action_batch_items_action
  ON parallel_action_batch_items(action_id, batch_id);

CREATE TABLE IF NOT EXISTS parallel_action_batch_events (
  id TEXT PRIMARY KEY,
  batch_id TEXT NOT NULL
    REFERENCES parallel_action_batches(id) ON DELETE CASCADE,
  version INTEGER NOT NULL CHECK (version > 0),
  event_type TEXT NOT NULL CHECK (
    event_type IN (
      'created', 'item_updated',
      'confirmations_completed_with_checks',
      'confirmations_completed_without_checks', 'checks_completed',
      'begin_commit', 'commit_succeeded', 'request_attention', 'supersede',
      'resume_confirmations', 'resume_checks', 'resume_ready'
    )
  ),
  actor_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  payload_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(payload_json)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(batch_id, version)
);
CREATE INDEX IF NOT EXISTS idx_parallel_action_batch_events_batch
  ON parallel_action_batch_events(batch_id, version, created_at, id);

CREATE TABLE IF NOT EXISTS parallel_action_regathers (
  id TEXT PRIMARY KEY,
  source_batch_id TEXT NOT NULL UNIQUE
    REFERENCES parallel_action_batches(id) ON DELETE CASCADE,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
  source_run_id TEXT NOT NULL REFERENCES campaign_module_runs(id) ON DELETE CASCADE,
  source_module_run_version INTEGER NOT NULL CHECK (source_module_run_version >= 0),
  source_state_version INTEGER NOT NULL CHECK (source_state_version >= 0),
  status TEXT NOT NULL DEFAULT 'gathering' CHECK (
    status IN ('gathering', 'queued', 'completed', 'cancelled')
  ),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  prepare_job_id TEXT UNIQUE REFERENCES auto_kp_jobs(id) ON DELETE SET NULL,
  resulting_batch_id TEXT UNIQUE
    REFERENCES parallel_action_batches(id) ON DELETE SET NULL,
  completion_kind TEXT CHECK (
    completion_kind IS NULL OR completion_kind IN ('batch', 'fallback')
  ),
  created_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  reason TEXT NOT NULL DEFAULT '' CHECK (length(reason) <= 2000),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at TEXT,
  CHECK (
    (status = 'completed' AND completion_kind IS NOT NULL
      AND completed_at IS NOT NULL)
    OR
    (status != 'completed' AND completion_kind IS NULL
      AND resulting_batch_id IS NULL AND completed_at IS NULL)
  )
);
CREATE INDEX IF NOT EXISTS idx_parallel_action_regathers_active
  ON parallel_action_regathers(campaign_id, session_id, status, updated_at, id);

CREATE TABLE IF NOT EXISTS parallel_action_regather_members (
  regather_id TEXT NOT NULL
    REFERENCES parallel_action_regathers(id) ON DELETE CASCADE,
  member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE RESTRICT,
  prior_action_id TEXT NOT NULL REFERENCES player_actions(id) ON DELETE RESTRICT,
  replacement_action_id TEXT UNIQUE REFERENCES player_actions(id) ON DELETE RESTRICT,
  auto_kp_requested INTEGER NOT NULL DEFAULT 0 CHECK (auto_kp_requested IN (0, 1)),
  submitted_at TEXT,
  PRIMARY KEY (regather_id, member_id),
  UNIQUE(regather_id, prior_action_id),
  CHECK (
    (replacement_action_id IS NULL AND auto_kp_requested = 0
      AND submitted_at IS NULL)
    OR
    (replacement_action_id IS NOT NULL AND submitted_at IS NOT NULL)
  )
);
CREATE INDEX IF NOT EXISTS idx_parallel_action_regather_member_active
  ON parallel_action_regather_members(member_id, regather_id);

CREATE TABLE IF NOT EXISTS parallel_action_regather_events (
  id TEXT PRIMARY KEY,
  regather_id TEXT NOT NULL
    REFERENCES parallel_action_regathers(id) ON DELETE CASCADE,
  version INTEGER NOT NULL CHECK (version > 0),
  event_type TEXT NOT NULL CHECK (
    event_type IN (
      'created', 'member_submitted', 'queued', 'reopened', 'completed', 'cancelled'
    )
  ),
  actor_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  payload_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(payload_json)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(regather_id, version)
);
CREATE INDEX IF NOT EXISTS idx_parallel_action_regather_events
  ON parallel_action_regather_events(regather_id, version, id);

CREATE TABLE IF NOT EXISTS campaign_setup_revisions (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  version INTEGER NOT NULL CHECK (version > 0),
  status TEXT NOT NULL CHECK (status IN ('pending', 'active', 'superseded')),
  config_json TEXT NOT NULL CHECK (json_valid(config_json)),
  created_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  activated_at TEXT,
  UNIQUE(campaign_id, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_campaign_setup_pending
  ON campaign_setup_revisions(campaign_id) WHERE status = 'pending';
CREATE UNIQUE INDEX IF NOT EXISTS uq_campaign_setup_active
  ON campaign_setup_revisions(campaign_id) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS session_zero_preferences (
  revision_id TEXT NOT NULL REFERENCES campaign_setup_revisions(id) ON DELETE CASCADE,
  member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE CASCADE,
  public_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(public_json)),
  private_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(private_json)),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (revision_id, member_id)
);

CREATE TABLE IF NOT EXISTS session_zero_confirmations (
  revision_id TEXT NOT NULL REFERENCES campaign_setup_revisions(id) ON DELETE CASCADE,
  member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE CASCADE,
  confirmed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (revision_id, member_id)
);

CREATE TABLE IF NOT EXISTS session_safety_events (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
  actor_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  response_kind TEXT NOT NULL CHECK (
    response_kind IN ('pause', 'fade', 'change', 'rewind')
  ),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'resolved')),
  run_id TEXT REFERENCES campaign_module_runs(id) ON DELETE SET NULL,
  prior_control_mode TEXT CHECK (
    prior_control_mode IS NULL OR
    prior_control_mode IN ('ai_assist', 'safety_paused', 'human_kp')
  ),
  public_message TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  resolved_at TEXT,
  resolved_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  resolution_kind TEXT CHECK (
    resolution_kind IS NULL OR
    resolution_kind IN ('fade', 'change', 'rewind', 'resume')
  )
);
CREATE INDEX IF NOT EXISTS idx_session_safety_active
  ON session_safety_events(session_id, status, created_at, id);

CREATE TABLE IF NOT EXISTS table_messages (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT NOT NULL UNIQUE,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
  sender_member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE RESTRICT,
  audience TEXT NOT NULL CHECK (
    audience IN ('table', 'party', 'announcement', 'direct')
  ),
  recipient_member_id TEXT REFERENCES session_members(id) ON DELETE RESTRICT,
  content TEXT NOT NULL CHECK (length(trim(content)) BETWEEN 1 AND 4000),
  client_message_id TEXT NOT NULL CHECK (
    length(client_message_id) BETWEEN 8 AND 200
  ),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK ((audience = 'direct') = (recipient_member_id IS NOT NULL)),
  UNIQUE(sender_member_id, client_message_id)
);
CREATE INDEX IF NOT EXISTS idx_table_messages_session_time
  ON table_messages(session_id, sequence DESC);
CREATE INDEX IF NOT EXISTS idx_table_messages_recipient
  ON table_messages(recipient_member_id, sequence DESC)
  WHERE recipient_member_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS campaign_episodes (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
  sequence_no INTEGER NOT NULL CHECK (sequence_no > 0),
  status TEXT NOT NULL CHECK (
    status IN ('prepared', 'in_progress', 'paused', 'ended')
  ),
  client_continue_id TEXT,
  event_start_rowid INTEGER NOT NULL CHECK (event_start_rowid > 0),
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  ended_at TEXT,
  version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
  UNIQUE(session_id, sequence_no),
  UNIQUE(session_id, client_continue_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_campaign_episodes_current
  ON campaign_episodes(session_id)
  WHERE status IN ('prepared', 'in_progress', 'paused');

CREATE TABLE IF NOT EXISTS session_continuity_snapshots (
  id TEXT PRIMARY KEY,
  episode_id TEXT NOT NULL UNIQUE REFERENCES campaign_episodes(id) ON DELETE CASCADE,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
  client_end_id TEXT NOT NULL,
  event_window_hash TEXT NOT NULL CHECK (length(event_window_hash) = 64),
  event_ids_json TEXT NOT NULL CHECK (json_valid(event_ids_json)),
  generation_cutoff TEXT NOT NULL,
  public_projection_json TEXT NOT NULL CHECK (json_valid(public_projection_json)),
  observer_projection_json TEXT NOT NULL CHECK (json_valid(observer_projection_json)),
  kp_projection_json TEXT NOT NULL CHECK (json_valid(kp_projection_json)),
  ended_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(session_id, client_end_id)
);
CREATE INDEX IF NOT EXISTS idx_continuity_snapshots_campaign_created
  ON session_continuity_snapshots(campaign_id, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS encounter_action_requests (
  id TEXT PRIMARY KEY,
  encounter_id TEXT NOT NULL REFERENCES coc7_encounters(id) ON DELETE CASCADE,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
  member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE RESTRICT,
  participant_id TEXT NOT NULL,
  client_action_id TEXT NOT NULL,
  encounter_version INTEGER NOT NULL CHECK (encounter_version >= 0),
  action_text TEXT NOT NULL,
  action_key TEXT NOT NULL,
  target_id TEXT,
  status TEXT NOT NULL CHECK (status IN (
    'awaiting_confirmation', 'needs_attention', 'confirmed',
    'committed', 'cancelled', 'failed'
  )),
  preview_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(preview_json)),
  prepared_command_json TEXT CHECK (
    prepared_command_json IS NULL OR json_valid(prepared_command_json)
  ),
  result_json TEXT CHECK (result_json IS NULL OR json_valid(result_json)),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  committed_at TEXT,
  UNIQUE(session_id, client_action_id)
);
CREATE INDEX IF NOT EXISTS idx_encounter_action_requests_active
  ON encounter_action_requests(encounter_id, member_id, status, updated_at);

CREATE TABLE IF NOT EXISTS encounter_automation_turns (
  id TEXT PRIMARY KEY,
  encounter_id TEXT NOT NULL REFERENCES coc7_encounters(id) ON DELETE CASCADE,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
  encounter_version INTEGER NOT NULL CHECK (encounter_version >= 0),
  participant_id TEXT NOT NULL,
  phase TEXT NOT NULL CHECK (phase IN ('enemy', 'idle_player')),
  policy TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'planned' CHECK (status IN (
    'planned', 'checkpointed', 'committed', 'needs_attention', 'cancelled'
  )),
  selection_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(selection_json)),
  prepared_command_json TEXT CHECK (
    prepared_command_json IS NULL OR json_valid(prepared_command_json)
  ),
  result_json TEXT CHECK (result_json IS NULL OR json_valid(result_json)),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  committed_at TEXT,
  UNIQUE(encounter_id, encounter_version, participant_id, phase)
);
CREATE INDEX IF NOT EXISTS idx_encounter_automation_active
  ON encounter_automation_turns(encounter_id, status, updated_at);

CREATE TABLE IF NOT EXISTS scenario_contract_overlays (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES scenario_run_states(run_id) ON DELETE CASCADE,
  proposal_key TEXT NOT NULL,
  sequence_no INTEGER NOT NULL CHECK (sequence_no > 0),
  status TEXT NOT NULL CHECK (status IN ('review_required', 'active', 'rejected')),
  base_contract_hash TEXT NOT NULL CHECK (length(base_contract_hash) = 64),
  base_state_version INTEGER NOT NULL CHECK (base_state_version >= 0),
  merged_contract_hash TEXT CHECK (
    merged_contract_hash IS NULL OR length(merged_contract_hash) = 64
  ),
  proposal_json TEXT NOT NULL CHECK (json_valid(proposal_json)),
  decision_json TEXT NOT NULL CHECK (json_valid(decision_json)),
  merged_contract_json TEXT CHECK (
    merged_contract_json IS NULL OR json_valid(merged_contract_json)
  ),
  created_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  reviewed_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  activated_at TEXT,
  UNIQUE(run_id, proposal_key),
  UNIQUE(run_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS idx_scenario_contract_overlays_run
  ON scenario_contract_overlays(run_id, sequence_no);

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

CREATE TABLE IF NOT EXISTS memory_curation_actions (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  classification TEXT NOT NULL
    CHECK (classification IN ('major', 'side', 'npc', 'clue', 'other')),
  importance INTEGER NOT NULL CHECK (importance BETWEEN 1 AND 5),
  hidden INTEGER NOT NULL DEFAULT 0 CHECK (hidden IN (0, 1)),
  reason TEXT NOT NULL CHECK (length(trim(reason)) BETWEEN 1 AND 1000),
  supersedes_action_id TEXT
    REFERENCES memory_curation_actions(id) ON DELETE RESTRICT,
  created_by_member_id TEXT NOT NULL
    REFERENCES session_members(id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(supersedes_action_id)
);

CREATE INDEX IF NOT EXISTS idx_memory_curation_memory_created
  ON memory_curation_actions(memory_id, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_memory_curation_campaign_created
  ON memory_curation_actions(campaign_id, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS session_recap_runs (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'completed')),
  event_window_hash TEXT NOT NULL CHECK (length(event_window_hash) = 64),
  event_ids_json TEXT NOT NULL CHECK (json_valid(event_ids_json)),
  generation_cutoff TEXT NOT NULL,
  source_model TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  repaired INTEGER NOT NULL DEFAULT 0 CHECK (repaired IN (0, 1)),
  created_by_member_id TEXT NOT NULL
    REFERENCES session_members(id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at TEXT,
  UNIQUE(session_id, event_window_hash)
);

CREATE TABLE IF NOT EXISTS session_recap_candidates (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES session_recap_runs(id) ON DELETE CASCADE,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  order_index INTEGER NOT NULL,
  text TEXT NOT NULL CHECK (length(trim(text)) BETWEEN 1 AND 2000),
  scope TEXT NOT NULL CHECK (
    scope IN (
      'campaign_fact', 'pc_major', 'pc_side', 'npc_interaction',
      'npc_relationship', 'location_fact', 'clue'
    )
  ),
  importance INTEGER NOT NULL CHECK (importance BETWEEN 1 AND 5),
  visibility TEXT NOT NULL CHECK (visibility IN ('player', 'table', 'kp')),
  pc_id TEXT REFERENCES player_characters(id) ON DELETE SET NULL,
  npc_id TEXT REFERENCES npcs(id) ON DELETE SET NULL,
  happened_at TEXT,
  source_event_ids_json TEXT NOT NULL CHECK (json_valid(source_event_ids_json)),
  rationale TEXT NOT NULL CHECK (length(trim(rationale)) BETWEEN 1 AND 2000),
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'approved', 'rejected')),
  review_payload_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(review_payload_json)),
  memory_id TEXT REFERENCES memories(id) ON DELETE SET NULL,
  reviewed_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  reviewed_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(run_id, order_index)
);

CREATE INDEX IF NOT EXISTS idx_session_recap_runs_session_created
  ON session_recap_runs(session_id, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_session_recap_candidates_draft
  ON session_recap_candidates(run_id, order_index)
  WHERE status = 'draft';

CREATE TABLE IF NOT EXISTS rule_sources (
  id TEXT PRIMARY KEY,
  ruleset_id TEXT NOT NULL,
  title TEXT NOT NULL,
  source_filename TEXT NOT NULL,
  source_hash TEXT NOT NULL,
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
  audience TEXT NOT NULL DEFAULT 'kp' CHECK (audience IN ('all', 'player', 'kp')),
  text TEXT NOT NULL,
  text_hash TEXT NOT NULL,
  extraction_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (extraction_status IN ('pending', 'processing', 'completed', 'failed')),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
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
CREATE UNIQUE INDEX IF NOT EXISTS idx_investigator_timeline_primary
  ON investigator_timeline_branches(investigator_id)
  WHERE is_primary = 1;
CREATE INDEX IF NOT EXISTS idx_investigator_timeline_branches_owner
  ON investigator_timeline_branches(investigator_id, status, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_investigator_participation_active_branch
  ON investigator_campaign_participations(branch_id)
  WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_investigator_participation_history
  ON investigator_campaign_participations(investigator_id, started_at, id);
CREATE INDEX IF NOT EXISTS idx_investigator_permanent_changes_owner
  ON investigator_permanent_change_proposals(investigator_id, status, created_at);
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
CREATE INDEX IF NOT EXISTS idx_player_actions_session ON player_actions(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_player_actions_campaign_status
  ON player_actions(campaign_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_auto_kp_jobs_campaign_status
  ON auto_kp_jobs(campaign_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_auto_kp_jobs_runnable
  ON auto_kp_jobs(status, next_run_at, created_at);
CREATE INDEX IF NOT EXISTS idx_realtime_events_session_cursor
  ON realtime_events(session_id, id);
CREATE INDEX IF NOT EXISTS idx_realtime_events_member_cursor
  ON realtime_events(member_id, id);
CREATE INDEX IF NOT EXISTS idx_realtime_tickets_expiry
  ON realtime_tickets(expires_at, consumed_at);
CREATE INDEX IF NOT EXISTS idx_turn_proposals_campaign ON turn_proposals(campaign_id, created_at);
CREATE INDEX IF NOT EXISTS idx_proposal_actions_proposal ON proposal_actions(proposal_id, created_at);
CREATE INDEX IF NOT EXISTS idx_world_expansion_materializations_campaign
  ON world_expansion_materializations(campaign_id, created_at);
CREATE INDEX IF NOT EXISTS idx_investigator_npc_encounters_investigator
  ON investigator_npc_encounters(investigator_id, npc_id, created_at);
CREATE INDEX IF NOT EXISTS idx_investigator_npc_encounters_npc
  ON investigator_npc_encounters(npc_id, campaign_id, created_at);
CREATE INDEX IF NOT EXISTS idx_npc_reappearance_appearances_campaign
  ON npc_reappearance_appearances(campaign_id, created_at);
CREATE INDEX IF NOT EXISTS idx_npc_hidden_appearance_campaign_created
  ON npc_hidden_appearance_resolutions(campaign_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_campaign_travel_locations_campaign
  ON campaign_travel_locations(campaign_id, normalized_name);
CREATE INDEX IF NOT EXISTS idx_campaign_travel_routes_campaign
  ON campaign_travel_routes(campaign_id, status, from_location_id, to_location_id);
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
CREATE INDEX IF NOT EXISTS idx_module_import_jobs_campaign_created
  ON module_import_jobs(campaign_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_module_import_jobs_status
  ON module_import_jobs(status, updated_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_campaign_module_runs_one_active
  ON campaign_module_runs(campaign_id)
  WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_campaign_module_runs_campaign_started
  ON campaign_module_runs(campaign_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_module_run_scene_events_run_created
  ON module_run_scene_events(run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_module_run_entity_states_run_status
  ON module_run_entity_states(run_id, status, entity_id);
CREATE INDEX IF NOT EXISTS idx_module_run_entity_events_run_created
  ON module_run_entity_state_events(run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_campaign_world_entities_campaign_kind
  ON campaign_world_entities(campaign_id, entity_kind, created_at);
CREATE INDEX IF NOT EXISTS idx_campaign_world_relations_source
  ON campaign_world_entity_relations(campaign_id, source_entity_id);
CREATE INDEX IF NOT EXISTS idx_world_entity_state_changes_entity
  ON campaign_world_entity_state_changes(entity_id, state_version);
CREATE INDEX IF NOT EXISTS idx_module_assets_module
  ON module_assets(module_id, created_at);
CREATE INDEX IF NOT EXISTS idx_module_assets_hash
  ON module_assets(content_hash);
CREATE INDEX IF NOT EXISTS idx_module_knowledge_status
  ON module_knowledge_candidates(module_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_module_knowledge_citations_chunk
  ON module_knowledge_citations(chunk_id);
CREATE INDEX IF NOT EXISTS idx_module_knowledge_citations_asset
  ON module_knowledge_citations(asset_id);
CREATE INDEX IF NOT EXISTS idx_module_entities_module_type
  ON module_entities(module_id, entity_type, name);
CREATE INDEX IF NOT EXISTS idx_module_entity_candidates_entity
  ON module_entity_candidates(entity_id);
CREATE INDEX IF NOT EXISTS idx_module_entity_candidates_candidate
  ON module_entity_candidates(candidate_id);
CREATE INDEX IF NOT EXISTS idx_module_entity_relations_source
  ON module_entity_relations(module_id, source_entity_id, predicate);
CREATE INDEX IF NOT EXISTS idx_module_entity_relations_target
  ON module_entity_relations(module_id, target_entity_id, predicate);
CREATE INDEX IF NOT EXISTS idx_memories_campaign_scope ON memories(campaign_id, scope);
CREATE INDEX IF NOT EXISTS idx_memories_pc ON memories(pc_id);
CREATE INDEX IF NOT EXISTS idx_memories_npc ON memories(npc_id);
CREATE INDEX IF NOT EXISTS idx_rule_sources_ruleset ON rule_sources(ruleset_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rule_sources_ruleset_hash
  ON rule_sources(ruleset_id, source_hash);
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

CREATE TABLE IF NOT EXISTS campaign_inventory_items (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  item_type TEXT NOT NULL,
  public_name TEXT NOT NULL,
  public_description TEXT NOT NULL DEFAULT '',
  publicly_listed INTEGER NOT NULL DEFAULT 0 CHECK (publicly_listed IN (0, 1)),
  quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity >= 0),
  is_unique INTEGER NOT NULL DEFAULT 0 CHECK (is_unique IN (0, 1)),
  holder_kind TEXT NOT NULL
    CHECK (holder_kind IN ('investigator', 'party', 'npc', 'location', 'loot', 'none')),
  holder_id TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'available'
    CHECK (state IN ('available', 'consumed', 'broken', 'lost')),
  equipped_slot TEXT,
  weight_units INTEGER NOT NULL DEFAULT 0 CHECK (weight_units >= 0),
  unit_value_minor INTEGER NOT NULL DEFAULT 0 CHECK (unit_value_minor >= 0),
  currency_code TEXT NOT NULL DEFAULT '',
  use_effect_json TEXT NOT NULL DEFAULT '{}',
  hidden_properties_json TEXT NOT NULL DEFAULT '{}',
  known_member_ids_json TEXT NOT NULL DEFAULT '[]',
  source_refs_json TEXT NOT NULL DEFAULT '[]',
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (is_unique = 0 OR quantity IN (0, 1)),
  CHECK (equipped_slot IS NULL OR holder_kind = 'investigator')
);
CREATE INDEX IF NOT EXISTS idx_inventory_items_holder
  ON campaign_inventory_items(campaign_id, holder_kind, holder_id, state);

CREATE TABLE IF NOT EXISTS campaign_currency_accounts (
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  account_kind TEXT NOT NULL
    CHECK (account_kind IN ('investigator', 'party', 'npc', 'vendor')),
  account_id TEXT NOT NULL,
  currency_code TEXT NOT NULL,
  balance_minor INTEGER NOT NULL DEFAULT 0 CHECK (balance_minor >= 0),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (campaign_id, account_kind, account_id, currency_code)
);

CREATE TABLE IF NOT EXISTS inventory_ledger_events (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  command_id TEXT NOT NULL,
  command_type TEXT NOT NULL,
  item_id TEXT REFERENCES campaign_inventory_items(id) ON DELETE SET NULL,
  actor_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  reason TEXT NOT NULL DEFAULT '',
  before_json TEXT NOT NULL DEFAULT '{}',
  after_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(campaign_id, command_id)
);
CREATE INDEX IF NOT EXISTS idx_inventory_ledger_campaign
  ON inventory_ledger_events(campaign_id, created_at, id);

CREATE TABLE IF NOT EXISTS inventory_transfer_offers (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  item_id TEXT NOT NULL REFERENCES campaign_inventory_items(id) ON DELETE CASCADE,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  from_investigator_id TEXT NOT NULL,
  to_investigator_id TEXT NOT NULL,
  offered_by_member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'accepted', 'declined', 'cancelled', 'stale')),
  item_version INTEGER NOT NULL CHECK (item_version > 0),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  resolved_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_inventory_transfer_pending_item
  ON inventory_transfer_offers(item_id) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS inventory_recipes (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  public_name TEXT NOT NULL,
  inputs_json TEXT NOT NULL DEFAULT '[]',
  output_template_json TEXT NOT NULL DEFAULT '{}',
  currency_cost_minor INTEGER NOT NULL DEFAULT 0 CHECK (currency_cost_minor >= 0),
  currency_code TEXT NOT NULL DEFAULT '',
  visibility TEXT NOT NULL DEFAULT 'table' CHECK (visibility IN ('table', 'kp')),
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
  source_refs_json TEXT NOT NULL DEFAULT '[]',
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS campaign_investigator_lifecycle (
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  investigator_id TEXT NOT NULL REFERENCES investigators(id) ON DELETE CASCADE,
  state TEXT NOT NULL DEFAULT 'active' CHECK (state IN (
    'active', 'incapacitated', 'dead', 'retired', 'departed', 'npc_controlled'
  )),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  last_source_event_id TEXT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (campaign_id, investigator_id)
);
CREATE TABLE IF NOT EXISTS campaign_member_presence (
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
  member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE CASCADE,
  state TEXT NOT NULL DEFAULT 'active' CHECK (state IN (
    'active', 'observing', 'temporarily_absent', 'departed', 'npc_controlled'
  )),
  investigator_id TEXT REFERENCES investigators(id) ON DELETE SET NULL,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (campaign_id, member_id)
);
CREATE TABLE IF NOT EXISTS character_lifecycle_requests (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
  member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE RESTRICT,
  investigator_id TEXT REFERENCES investigators(id) ON DELETE SET NULL,
  replacement_investigator_id TEXT REFERENCES investigators(id) ON DELETE SET NULL,
  action TEXT NOT NULL CHECK (action IN (
    'observe', 'replace', 'retire', 'temporary_leave', 'npc_control', 'return', 'resurrect'
  )),
  status TEXT NOT NULL DEFAULT 'awaiting_player' CHECK (status IN (
    'awaiting_player', 'applied', 'rejected', 'cancelled'
  )),
  reason TEXT NOT NULL,
  base_lifecycle_version INTEGER,
  proposed_by_member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE RESTRICT,
  decided_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  decided_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_lifecycle_request_pending
  ON character_lifecycle_requests(campaign_id, member_id)
  WHERE status = 'awaiting_player';
CREATE TABLE IF NOT EXISTS character_lifecycle_events (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  session_id TEXT REFERENCES campaign_sessions(id) ON DELETE SET NULL,
  investigator_id TEXT REFERENCES investigators(id) ON DELETE SET NULL,
  member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  command_id TEXT NOT NULL,
  action TEXT NOT NULL,
  from_state TEXT,
  to_state TEXT NOT NULL,
  reason TEXT NOT NULL,
  source_event_id TEXT,
  actor_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  public_summary TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(campaign_id, command_id)
);
CREATE INDEX IF NOT EXISTS idx_lifecycle_events_campaign
  ON character_lifecycle_events(campaign_id, created_at, id);

CREATE TABLE IF NOT EXISTS campaign_objectives (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  public_description TEXT NOT NULL DEFAULT '',
  kp_notes TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN (
    'open', 'blocked', 'completed', 'failed', 'abandoned'
  )),
  visibility TEXT NOT NULL DEFAULT 'table' CHECK (visibility IN ('table', 'kp')),
  source_refs_json TEXT NOT NULL DEFAULT '[]',
  created_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_campaign_objectives_projection
  ON campaign_objectives(campaign_id, visibility, status, updated_at, id);
CREATE TABLE IF NOT EXISTS campaign_objective_events (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  objective_id TEXT NOT NULL REFERENCES campaign_objectives(id) ON DELETE CASCADE,
  command_id TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT NOT NULL,
  public_progress TEXT NOT NULL DEFAULT '',
  kp_notes TEXT NOT NULL DEFAULT '',
  actor_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  source_refs_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(campaign_id, command_id)
);
CREATE INDEX IF NOT EXISTS idx_campaign_objective_events
  ON campaign_objective_events(campaign_id, objective_id, created_at, id);

CREATE TABLE IF NOT EXISTS director_help_audit_events (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL UNIQUE,
  attempt_id TEXT NOT NULL,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  run_id TEXT NOT NULL REFERENCES campaign_module_runs(id) ON DELETE CASCADE,
  requested_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  request_id TEXT NOT NULL CHECK (length(request_id) BETWEEN 8 AND 200),
  event_type TEXT NOT NULL CHECK (event_type IN (
    'requested', 'completed', 'failed', 'cancelled_client',
    'cancelled_control', 'rejected_busy'
  )),
  question TEXT,
  advice_json TEXT,
  response_hash TEXT,
  error_code TEXT,
  duration_ms INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (
    (event_type = 'requested'
      AND question IS NOT NULL
      AND length(trim(question)) BETWEEN 1 AND 2000
      AND advice_json IS NULL
      AND response_hash IS NULL
      AND error_code IS NULL
      AND duration_ms IS NULL)
    OR
    (event_type = 'completed'
      AND question IS NULL
      AND advice_json IS NOT NULL
      AND json_valid(advice_json)
      AND json_type(advice_json) = 'object'
      AND response_hash IS NOT NULL
      AND length(response_hash) = 64
      AND response_hash NOT GLOB '*[^0-9a-f]*'
      AND error_code IS NULL
      AND duration_ms IS NOT NULL
      AND duration_ms >= 0)
    OR
    (event_type IN (
        'failed', 'cancelled_client', 'cancelled_control', 'rejected_busy'
      )
      AND question IS NULL
      AND advice_json IS NULL
      AND response_hash IS NULL
      AND error_code IS NOT NULL
      AND length(trim(error_code)) BETWEEN 1 AND 160
      AND error_code GLOB '[a-z]*'
      AND error_code NOT GLOB '*[^a-z0-9_]*'
      AND duration_ms IS NOT NULL
      AND duration_ms >= 0)
  )
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_director_help_audit_requested_attempt
  ON director_help_audit_events(attempt_id)
  WHERE event_type = 'requested';
CREATE UNIQUE INDEX IF NOT EXISTS ux_director_help_audit_terminal_attempt
  ON director_help_audit_events(attempt_id)
  WHERE event_type <> 'requested';
CREATE INDEX IF NOT EXISTS idx_director_help_audit_campaign_requested_sequence
  ON director_help_audit_events(campaign_id, sequence DESC)
  WHERE event_type = 'requested';
"""

OWNED_DEFAULT_DB_PATH = Path("data/ai_kp.sqlite3")


def connect(
    db_path: Path | str,
    *,
    synchronous: str = "FULL",
) -> sqlite3.Connection:
    normalized_synchronous = synchronous.upper()
    if normalized_synchronous not in {"FULL", "NORMAL"}:
        raise ValueError("SQLite synchronous mode must be FULL or NORMAL")

    raw_path = str(db_path)
    is_memory = raw_path == ":memory:"
    sqlite_path: Path | str
    if is_memory:
        sqlite_path = raw_path
    else:
        sqlite_path = Path(db_path)
        directory_was_missing = not sqlite_path.parent.exists()
        sqlite_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory_was_missing or sqlite_path == OWNED_DEFAULT_DB_PATH:
            os.chmod(sqlite_path.parent, 0o700)
        descriptor = os.open(sqlite_path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        os.chmod(sqlite_path, 0o600)

    connection = sqlite3.connect(sqlite_path, timeout=5.0, check_same_thread=False)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        if not is_memory:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(f"PRAGMA synchronous = {normalized_synchronous}")
            for suffix in ("", "-wal", "-shm"):
                private_file = Path(f"{sqlite_path}{suffix}")
                if private_file.exists():
                    os.chmod(private_file, 0o600)
    except Exception:
        connection.close()
        raise
    return connection


def init_db(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    apply_migrations(connection)
    connection.commit()


@contextmanager
def db_session(
    db_path: Path | str,
    *,
    synchronous: str = "FULL",
) -> Iterator[sqlite3.Connection]:
    connection = connect(db_path, synchronous=synchronous)
    try:
        init_db(connection)
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
