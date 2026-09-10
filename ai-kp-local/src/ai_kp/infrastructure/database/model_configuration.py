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
        semantic_profile: str,
    ) -> dict:
        self.connection.execute(
            """
            INSERT INTO model_configuration
              (id, provider_type, base_url, api_key, model, local_model_path, local_port,
               semantic_profile)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              provider_type = excluded.provider_type,
              base_url = excluded.base_url,
              api_key = excluded.api_key,
              model = excluded.model,
              local_model_path = excluded.local_model_path,
              local_port = excluded.local_port,
              semantic_profile = excluded.semantic_profile,
              version = model_configuration.version + 1,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                provider_type,
                base_url,
                api_key,
                model,
                local_model_path,
                local_port,
                semantic_profile,
            ),
        )
        result = self.get_model_configuration()
        if result is None:
            raise RuntimeError("Model configuration was not saved")
        return result

    def get_image_model_configuration(self) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM image_model_configuration WHERE id = 1"
        ).fetchone()
        return dict(row) if row is not None else None

    def save_image_model_configuration(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout_seconds: float,
    ) -> dict:
        self.connection.execute(
            """
            INSERT INTO image_model_configuration
              (id, base_url, api_key, model, timeout_seconds)
            VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              base_url = excluded.base_url,
              api_key = excluded.api_key,
              model = excluded.model,
              timeout_seconds = excluded.timeout_seconds,
              updated_at = CURRENT_TIMESTAMP
            """,
            (base_url, api_key, model, timeout_seconds),
        )
        result = self.get_image_model_configuration()
        if result is None:
            raise RuntimeError("Image model configuration was not saved")
        return result
