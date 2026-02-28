from __future__ import annotations

from pathlib import Path

import duckdb

from medicare_navigator.config import settings


class DuckDBConnection:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings.duckdb_path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.path))

    def execute(self, sql: str, params: list | None = None) -> duckdb.DuckDBPyConnection:
        conn = self.connect()
        try:
            if params:
                conn.execute(sql, params)
            else:
                conn.execute(sql)
            return conn
        except Exception:
            conn.close()
            raise

    def fetchone(self, sql: str, params: list | None = None):
        conn = self.connect()
        try:
            if params:
                return conn.execute(sql, params).fetchone()
            return conn.execute(sql).fetchone()
        finally:
            conn.close()

    def fetchall(self, sql: str, params: list | None = None):
        conn = self.connect()
        try:
            if params:
                return conn.execute(sql, params).fetchall()
            return conn.execute(sql).fetchall()
        finally:
            conn.close()
