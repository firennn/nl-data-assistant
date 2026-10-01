import sqlite3

import pytest

from shared.db import (
    QueryError,
    QueryTimeoutError,
    UnsafeQueryError,
    connect_read_only,
    run_query,
    validate_read_only,
)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM orders",
        "select count(*) from orders;",
        "  WITH t AS (SELECT 1 AS x) SELECT x FROM t",
        "(SELECT 1)",
        "SELECT * FROM orders WHERE status = 'drop table; delete'",  # keywords inside a string
        "SELECT replace(city, 'a', 'b') FROM customers",  # replace() is a function, not a write
        "SELECT created_date, updated FROM t",  # names containing keywords as substrings
        "SELECT 1 -- DROP TABLE orders",  # keyword only in a comment
    ],
)
def test_validate_accepts_read_only(sql):
    assert validate_read_only(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "   ",
        "-- just a comment",
        "DROP TABLE orders",
        "DELETE FROM orders",
        "UPDATE orders SET status = 'x'",
        "INSERT INTO orders VALUES (1)",
        "REPLACE INTO orders VALUES (1)",
        "ALTER TABLE orders ADD COLUMN x",
        "CREATE TABLE x (a)",
        "ATTACH DATABASE 'other.db' AS other",
        "PRAGMA table_info(orders)",
        "VACUUM",
        "SELECT 1; DELETE FROM orders",
        "SELECT 1; SELECT 2",
        "SELECT 1 /* hidden */; DROP TABLE orders",
        "WITH t AS (SELECT 1) DELETE FROM orders",
        "WITH t AS (SELECT 1) INSERT INTO orders SELECT * FROM t",
        "EXPLAIN SELECT 1",
    ],
)
def test_validate_rejects_unsafe(sql):
    with pytest.raises(UnsafeQueryError):
        validate_read_only(sql)


def test_validate_strips_trailing_semicolon():
    assert validate_read_only("SELECT 1;  ") == "SELECT 1"


def test_run_query_returns_dataframe(sample_db):
    df = run_query(
        "SELECT status, COUNT(*) AS n FROM orders GROUP BY status ORDER BY status",
        db_path=sample_db,
    )
    assert list(df.columns) == ["status", "n"]
    assert df.to_dict("records") == [
        {"status": "canceled", "n": 1},
        {"status": "delivered", "n": 2},
    ]
    assert df.attrs["truncated"] is False


def test_run_query_with_params(sample_db):
    df = run_query("SELECT order_id FROM orders WHERE status = ?", ["canceled"], db_path=sample_db)
    assert df["order_id"].tolist() == ["o3"]


def test_run_query_truncates(sample_db):
    df = run_query("SELECT * FROM order_items", db_path=sample_db, max_rows=2)
    assert len(df) == 2
    assert df.attrs["truncated"] is True


def test_run_query_blocks_unsafe_before_running(sample_db):
    with pytest.raises(UnsafeQueryError):
        run_query("DELETE FROM orders", db_path=sample_db)
    assert len(run_query("SELECT * FROM orders", db_path=sample_db)) == 3


def test_run_query_reports_sql_errors(sample_db):
    with pytest.raises(QueryError, match="no such column"):
        run_query("SELECT missing_column FROM orders", db_path=sample_db)


def test_run_query_timeout(sample_db):
    endless = "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT max(i) FROM n"
    with pytest.raises(QueryTimeoutError):
        run_query(endless, db_path=sample_db, timeout_s=0.2)


def test_connection_is_read_only_even_without_validator(sample_db):
    conn = connect_read_only(sample_db)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM orders")
    finally:
        conn.close()


def test_missing_database_has_helpful_message(tmp_path):
    with pytest.raises(FileNotFoundError, match="data.build_db"):
        run_query("SELECT 1", db_path=tmp_path / "missing.db")


def test_connection_does_not_trust_the_schema(sample_db):
    conn = connect_read_only(sample_db)
    try:
        assert conn.execute("PRAGMA trusted_schema").fetchone()[0] == 0
    finally:
        conn.close()


def test_view_in_the_file_cannot_call_functions_with_side_effects(tmp_path):
    db = tmp_path / "untrusted.db"
    with sqlite3.connect(db) as setup:
        setup.execute("CREATE TABLE t (x)")
        setup.execute("INSERT INTO t VALUES (1)")
        setup.execute("CREATE VIEW v AS SELECT side_effect(x) AS y FROM t")
    setup.close()

    calls = []
    conn = connect_read_only(db)
    try:
        conn.create_function("side_effect", 1, lambda x: calls.append(x) or x)
        with pytest.raises(sqlite3.OperationalError, match="unsafe use of side_effect"):
            conn.execute("SELECT * FROM v").fetchall()
    finally:
        conn.close()
    assert calls == []
