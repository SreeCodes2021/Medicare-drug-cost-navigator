from unittest.mock import MagicMock, patch

from medicare_navigator.storage.connection import DuckDBConnection


def test_fetchall_opens_and_closes_connection(tmp_path):
    db_path = tmp_path / "navigator.duckdb"
    with patch("medicare_navigator.storage.connection.duckdb.connect") as mock_connect:
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        mock_connect.return_value = conn
        DuckDBConnection(path=db_path).fetchall("SELECT 1")
        mock_connect.assert_called_once_with(str(db_path), read_only=False)
        conn.close.assert_called_once()


def test_fetchone_opens_and_closes_connection(tmp_path):
    db_path = tmp_path / "navigator.duckdb"
    with patch("medicare_navigator.storage.connection.duckdb.connect") as mock_connect:
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = (1,)
        mock_connect.return_value = conn
        DuckDBConnection(path=db_path).fetchone("SELECT 1")
        mock_connect.assert_called_once_with(str(db_path), read_only=False)
        conn.close.assert_called_once()


def test_connect_opens_database(tmp_path):
    db_path = tmp_path / "navigator.duckdb"
    with patch("medicare_navigator.storage.connection.duckdb.connect") as mock_connect:
        mock_connect.return_value = MagicMock()
        DuckDBConnection(path=db_path).connect()
        mock_connect.assert_called_once_with(str(db_path), read_only=False)


def test_fetchone_works_while_write_connection_open(tmp_path):
    """Regression: mixed read_only modes on the same file raise ConnectionException."""
    import duckdb

    db_path = tmp_path / "navigator.duckdb"
    write_conn = duckdb.connect(str(db_path))
    write_conn.execute("CREATE TABLE t (id INTEGER)")
    write_conn.execute("INSERT INTO t VALUES (1)")
    db = DuckDBConnection(path=db_path)
    assert db.fetchone("SELECT id FROM t") == (1,)
    write_conn.close()
