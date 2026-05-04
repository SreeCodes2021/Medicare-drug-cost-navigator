from unittest.mock import MagicMock, patch

from medicare_navigator.storage.connection import DuckDBConnection


def test_fetchall_uses_read_only_connection(tmp_path):
    db_path = tmp_path / "navigator.duckdb"
    with patch("medicare_navigator.storage.connection.duckdb.connect") as mock_connect:
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        mock_connect.return_value = conn
        DuckDBConnection(path=db_path).fetchall("SELECT 1")
        mock_connect.assert_called_once_with(str(db_path), read_only=True)


def test_fetchone_uses_read_only_connection(tmp_path):
    db_path = tmp_path / "navigator.duckdb"
    with patch("medicare_navigator.storage.connection.duckdb.connect") as mock_connect:
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = (1,)
        mock_connect.return_value = conn
        DuckDBConnection(path=db_path).fetchone("SELECT 1")
        mock_connect.assert_called_once_with(str(db_path), read_only=True)


def test_connect_write_mode_by_default(tmp_path):
    db_path = tmp_path / "navigator.duckdb"
    with patch("medicare_navigator.storage.connection.duckdb.connect") as mock_connect:
        mock_connect.return_value = MagicMock()
        DuckDBConnection(path=db_path).connect()
        mock_connect.assert_called_once_with(str(db_path))
