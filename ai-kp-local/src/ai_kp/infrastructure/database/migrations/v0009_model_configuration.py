"""Migration 9: model configuration."""

import sqlite3

VERSION = 9
NAME = "add_model_configuration"


DDL = """
CREATE TABLE IF NOT EXISTS model_configuration (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  provider_type TEXT NOT NULL CHECK (provider_type IN ('openai_compatible', 'local_mlx')),
  base_url TEXT,
  api_key TEXT,
  model TEXT NOT NULL,
  local_model_path TEXT,
  local_port INTEGER NOT NULL DEFAULT 8011 CHECK (local_port BETWEEN 1024 AND 65535),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(DDL)
