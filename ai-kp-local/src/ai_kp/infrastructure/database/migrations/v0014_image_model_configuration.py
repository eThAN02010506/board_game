"""Migration 14: independently persisted image-model configuration."""

import sqlite3


VERSION = 14
NAME = "add_image_model_configuration"


DDL = """
CREATE TABLE IF NOT EXISTS image_model_configuration (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  base_url TEXT NOT NULL,
  api_key TEXT,
  model TEXT NOT NULL,
  timeout_seconds REAL NOT NULL DEFAULT 300
    CHECK (timeout_seconds BETWEEN 10 AND 1800),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(DDL)
