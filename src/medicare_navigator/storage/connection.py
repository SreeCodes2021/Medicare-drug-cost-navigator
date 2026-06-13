from __future__ import annotations

from pathlib import Path

import duckdb

from medicare_navigator.config import settings


def _is_missing_table_error(exc: BaseException) -> bool:
    if not isinstance(exc, duckdb.CatalogException):
        return False
    message = str(exc).lower()
    return "does not exist" in message and "table" in message


class DuckDBConnection:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings.duckdb_path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
        if read_only:
            return duckdb.connect(str(self.path), read_only=True)
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
        conn = self.connect(read_only=True)
        try:
            if params:
                return conn.execute(sql, params).fetchone()
            return conn.execute(sql).fetchone()
        except duckdb.CatalogException as exc:
            if _is_missing_table_error(exc):
                return None
            raise
        finally:
            conn.close()

    def fetchall(self, sql: str, params: list | None = None):
        conn = self.connect(read_only=True)
        try:
            if params:
                return conn.execute(sql, params).fetchall()
            return conn.execute(sql).fetchall()
        except duckdb.CatalogException as exc:
            if _is_missing_table_error(exc):
                return []
            raise
        finally:
            conn.close()
