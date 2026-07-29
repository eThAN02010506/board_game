from pathlib import Path

from ai_kp.api.main import create_app
from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.database.schema import connect, init_db


def test_app_startup_recovers_only_interrupted_knowledge_claims(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "startup-recovery.sqlite3"
    connection = connect(db_path)
    try:
        init_db(connection)
        connection.execute(
            """
            INSERT INTO rule_sources
              (id, ruleset_id, title, source_filename, source_hash, page_count)
            VALUES ('rulesource_recovery', 'coc7', '恢复规则', 'rules.pdf', ?, 1)
            """,
            ("a" * 64,),
        )
        connection.execute(
            """
            INSERT INTO rule_sources
              (id, ruleset_id, title, source_filename, source_hash, page_count, status)
            VALUES (
              'rulesource_index_recovery', 'coc7', '恢复索引', 'index.pdf', ?, 1,
              'indexing'
            )
            """,
            ("b" * 64,),
        )
        connection.executemany(
            """
            INSERT INTO rule_ingestion_runs
              (id, source_id, status, stage, error_text)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                (
                    "run_rule_processing",
                    "rulesource_recovery",
                    "running",
                    "rule_extraction",
                    None,
                ),
                (
                    "run_index_processing",
                    "rulesource_index_recovery",
                    "running",
                    "minirag_index",
                    None,
                ),
                (
                    "run_rule_completed",
                    "rulesource_recovery",
                    "completed",
                    "rule_extraction",
                    None,
                ),
            ),
        )
        for chunk_id, status, order_index, attempt_count in (
            ("rule_processing", "processing", 0, 3),
            ("rule_failed", "failed", 1, 2),
            ("rule_completed", "completed", 2, 1),
        ):
            connection.execute(
                """
                INSERT INTO rule_chunks
                  (id, source_id, page_start, page_end, order_index, text, text_hash,
                   extraction_status, attempt_count)
                VALUES (?, 'rulesource_recovery', 1, 1, ?, ?, ?, ?, ?)
                """,
                (
                    chunk_id,
                    order_index,
                    chunk_id,
                    str(order_index) * 64,
                    status,
                    attempt_count,
                ),
            )
        connection.execute(
            """
            INSERT INTO modules (id, title, source_type)
            VALUES ('module_recovery', '恢复模组', 'plaintext')
            """
        )
        for chunk_id, status, order_index, attempt_count in (
            ("module_processing", "processing", 0, 3),
            ("module_failed", "failed", 1, 2),
            ("module_completed", "completed", 2, 1),
        ):
            connection.execute(
                """
                INSERT INTO module_chunks
                  (id, module_id, title, text, visibility, order_index, knowledge_status,
                   attempt_count)
                VALUES (?, 'module_recovery', ?, ?, 'kp', ?, ?, ?)
                """,
                (
                    chunk_id,
                    chunk_id,
                    chunk_id,
                    order_index,
                    status,
                    attempt_count,
                ),
            )
        connection.commit()
    finally:
        connection.close()

    create_app(
        Settings(
            db_path=db_path,
            module_asset_root=tmp_path / "module-assets",
        )
    )

    observer = connect(db_path)
    try:
        rule_statuses = {
            row["id"]: (row["extraction_status"], row["attempt_count"])
            for row in observer.execute(
                "SELECT id, extraction_status, attempt_count FROM rule_chunks"
            ).fetchall()
        }
        module_statuses = {
            row["id"]: (row["knowledge_status"], row["attempt_count"])
            for row in observer.execute(
                "SELECT id, knowledge_status, attempt_count FROM module_chunks"
            ).fetchall()
        }
        ingestion_runs = {
            row["id"]: (row["status"], row["error_text"])
            for row in observer.execute(
                "SELECT id, status, error_text FROM rule_ingestion_runs"
            ).fetchall()
        }
        index_source_status = observer.execute(
            "SELECT status FROM rule_sources WHERE id = 'rulesource_index_recovery'"
        ).fetchone()["status"]
    finally:
        observer.close()

    assert rule_statuses == {
        "rule_completed": ("completed", 1),
        "rule_failed": ("failed", 2),
        "rule_processing": ("pending", 3),
    }
    assert module_statuses == {
        "module_completed": ("completed", 1),
        "module_failed": ("failed", 2),
        "module_processing": ("pending", 3),
    }
    assert ingestion_runs["run_rule_processing"][0] == "failed"
    assert "interrupted" in ingestion_runs["run_rule_processing"][1].lower()
    assert ingestion_runs["run_index_processing"][0] == "failed"
    assert "interrupted" in ingestion_runs["run_index_processing"][1].lower()
    assert ingestion_runs["run_rule_completed"] == ("completed", None)
    assert index_source_status == "failed"
