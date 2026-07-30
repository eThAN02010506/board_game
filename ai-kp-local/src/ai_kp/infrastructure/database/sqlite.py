"""Canonical shared SQLite repository base adapter."""

import sqlite3
import time

from ai_kp.observability import operational_telemetry


class SQLiteRepository:
    """Common connection holder for repositories sharing one SQLite transaction."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()

    def begin_immediate(self) -> None:
        if not self.connection.in_transaction:
            started = time.perf_counter()
            try:
                self.connection.execute("BEGIN IMMEDIATE")
            finally:
                operational_telemetry.record_lock_wait(
                    (time.perf_counter() - started) * 1000
                )
