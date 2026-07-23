"""SQLite adapter for the singleton local model configuration."""

import sqlite3


class ModelConfigurationRepository:
    connection: sqlite3.Connection

    def get_model_configuration(self) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM model_configuration WHERE id = 1"
        ).fetchone()
        return dict(row) if row is not None else None

    def save_model_configuration(
        self,
        *,
        provider_type: str,
        base_url: str | None,
        api_key: str | None,
        model: str,
        local_model_path: str | None,
        local_port: int,
    ) -> dict:
        self.connection.execute(
            """
            INSERT INTO model_configuration
              (id, provider_type, base_url, api_key, model, local_model_path, local_port)
            VALUES (1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              provider_type = excluded.provider_type,
              base_url = excluded.base_url,
              api_key = excluded.api_key,
              model = excluded.model,
              local_model_path = excluded.local_model_path,
              local_port = excluded.local_port,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                provider_type,
                base_url,
                api_key,
                model,
                local_model_path,
                local_port,
            ),
        )
        result = self.get_model_configuration()
        if result is None:
            raise RuntimeError("Model configuration was not saved")
        return result
