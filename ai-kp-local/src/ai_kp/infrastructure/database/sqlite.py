"""Canonical shared SQLite repository base adapter."""

import sqlite3


class SQLiteRepository:
    """Common connection holder for repositories sharing one SQLite transaction."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def commit(self) -> None:
        self.connection.commit()
