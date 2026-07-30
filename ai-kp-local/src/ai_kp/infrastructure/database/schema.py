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
