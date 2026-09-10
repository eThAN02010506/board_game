from pathlib import Path

from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.infrastructure.database.integrity import fts_integrity_issues
from ai_kp.platform.modules.ingestion import ModuleChunk


def test_fts_integrity_check_covers_module_search_shadow_tables(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "fts-integrity.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Integrity test")
        repo.create_module(
            campaign["id"],
            "Source",
            [
                ModuleChunk(
                    title="Scene",
                    text="The investigator searches the archive for a hidden record.",
                    visibility="kp",
                    order_index=0,
                )
            ],
        )
        connection.commit()
        assert fts_integrity_issues(connection) == []

        connection.execute(
            """
            UPDATE module_search_fts_data SET block = X'FFFFFFFFFFFFFFFF'
            WHERE id = (
              SELECT MAX(id) FROM module_search_fts_data WHERE id > 10
            )
            """
        )

        issues = fts_integrity_issues(connection)
        assert issues
        assert issues[0]["table"] == "module_search_fts"
