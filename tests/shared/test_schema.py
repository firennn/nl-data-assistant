import sqlite3

import pytest

from shared.db import QueryError, QueryTimeoutError
from shared.schema import (
    SCHEMA_COLUMN_CAPS,
    SCHEMA_MAX_CHARS,
    SCHEMA_OMITTED_TABLE_NAMES,
    ColumnInfo,
    SchemaInfo,
    TableInfo,
    describe_schema,
    schema_to_prompt,
)


def test_describe_schema_lists_tables_without_internal(sample_db):
    schema = describe_schema(sample_db)
    assert "orders" in schema.table_names
    assert "_schema_docs" not in schema.table_names


def test_describe_schema_columns_keys_and_docs(sample_db):
    orders = describe_schema(sample_db).table("orders")
    assert orders.row_count == 3
    assert orders.description == "One row per order."
    assert orders.column("order_id").is_pk
    assert orders.column("customer_id").fk_ref == "customers.customer_id"
    assert orders.column("status").description.startswith("Order status")
    assert set(orders.column("status").sample_values) <= {"delivered", "canceled"}


def test_describe_schema_filter_and_no_samples(sample_db):
    schema = describe_schema(sample_db, tables=["products"], sample_values=0)
    assert schema.table_names == ["products"]
    assert all(c.sample_values == [] for c in schema.tables[0].columns)


def test_describe_schema_unknown_table(sample_db):
    with pytest.raises(KeyError):
        describe_schema(sample_db, tables=["nope"])


def test_schema_to_prompt(sample_db):
    text = schema_to_prompt(describe_schema(sample_db))
    assert "TABLE orders (3 rows): One row per order." in text
    assert "customer_id TEXT FK -> customers.customer_id" in text
    assert "samples:" in text
    assert "samples:" not in schema_to_prompt(describe_schema(sample_db), include_samples=False)


def test_schema_to_prompt_skips_samples_for_id_columns(sample_db):
    lines = schema_to_prompt(describe_schema(sample_db)).splitlines()
    order_id_line = next(line for line in lines if line.strip().startswith("- order_id"))
    status_line = next(line for line in lines if line.strip().startswith("- status"))
    assert "samples:" not in order_id_line
    assert "samples:" in status_line


# --- time limit and unreadable files -------------------------------------------------------


def make_slow_db(path):
    """A table whose only sampled column is always NULL, so sampling scans every row."""
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE big (a INTEGER, empty_col TEXT)")
        conn.execute(
            "INSERT INTO big (a) WITH RECURSIVE n(i) AS "
            "(SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 200000) SELECT i FROM n"
        )
    conn.close()
    return path


def test_describe_schema_time_limit(tmp_path, monkeypatch):
    db = make_slow_db(tmp_path / "slow.db")
    assert describe_schema(db).table("big").row_count == 200_000  # fine with the default

    # A clock that moves one second per reading, so the test does not depend on machine speed.
    import shared.schema as module

    ticks = iter(range(10**9))
    monkeypatch.setattr(module.time, "monotonic", lambda: float(next(ticks)))
    with pytest.raises(QueryTimeoutError, match="took longer than 5s"):
        describe_schema(db, timeout_s=5)


def test_describe_schema_rejects_a_file_that_is_not_a_database(tmp_path):
    fake = tmp_path / "people.sqlite"
    fake.write_text("name,age\nana,31\n")
    with pytest.raises(QueryError, match="Could not read the database: file is not a database"):
        describe_schema(fake)


def test_timeout_is_a_query_error():
    assert issubclass(QueryTimeoutError, QueryError)


# --- size limit ----------------------------------------------------------------------------


def wide_schema(tables=60, columns=40, described=True) -> SchemaInfo:
    result = []
    for t in range(tables):
        cols = [ColumnInfo(f"t{t:02d}_id", "TEXT", False, True)]
        if t:
            cols.append(ColumnInfo("parent_id", "TEXT", True, False, fk_ref=f"t{t - 1:02d}.id"))
        cols += [
            ColumnInfo(
                f"measure_number_{c:02d}",
                "REAL",
                True,
                False,
                description=f"Measure {c} of table {t}" if described else None,
                sample_values=[1.5, 2.5, 3.5] if described else [],
            )
            for c in range(columns - len(cols))
        ]
        description = f"Table number {t}" if described else None
        result.append(TableInfo(f"table_{t:02d}", 1000, cols, description=description))
    return SchemaInfo(result)


def test_limit_leaves_a_schema_that_fits_unchanged(sample_db):
    schema = describe_schema(sample_db)
    full = schema_to_prompt(schema)
    assert schema_to_prompt(schema, max_chars=SCHEMA_MAX_CHARS) == full
    assert schema_to_prompt(schema, max_chars=len(full)) == full


def test_samples_are_dropped_first(sample_db):
    schema = describe_schema(sample_db)
    without_samples = schema_to_prompt(schema, include_samples=False)
    text = schema_to_prompt(schema, max_chars=len(without_samples))
    assert text == without_samples
    assert "One row per order." in text  # descriptions kept


def test_descriptions_are_dropped_next():
    schema = wide_schema(tables=2, columns=5)
    no_descriptions = "TABLE table_00 (1000 rows)"
    text = schema_to_prompt(schema, max_chars=400)
    assert len(text) <= 400
    assert "samples:" not in text and "Measure" not in text and no_descriptions in text
    assert "more columns" not in text


def test_wide_schema_fits_and_says_what_was_left_out():
    text = schema_to_prompt(wide_schema(), max_chars=SCHEMA_MAX_CHARS)
    assert len(text) <= SCHEMA_MAX_CHARS
    assert "samples:" not in text and "Measure" not in text
    assert f"  ... {40 - SCHEMA_COLUMN_CAPS[-1]} more columns not shown" in text
    assert "t00_id TEXT PK" in text
    assert "parent_id TEXT FK -> t00.id" in text  # keys are always kept
    last = text.rstrip().splitlines()[-1]
    assert last.startswith("... ") and "more tables not shown: " in last
    assert last.endswith("others")  # more hidden tables than listed names
    assert last.count(",") == SCHEMA_OMITTED_TABLE_NAMES  # 20 names + "and N others"


def test_column_caps_are_tried_from_large_to_small():
    # No descriptions or samples, so the first two steps cannot make the text shorter.
    schema = wide_schema(tables=3, columns=40, described=False)
    full = schema_to_prompt(schema)
    text = schema_to_prompt(schema, max_chars=len(full) - 1)
    assert f"  ... {40 - SCHEMA_COLUMN_CAPS[0]} more columns not shown" in text
    assert "more tables" not in text
    assert len(text) <= len(full) - 1


def test_single_huge_table_is_cut_with_a_note():
    cols = [ColumnInfo("x" * 300 + str(i), "TEXT", False, i == 0) for i in range(50)]
    schema = SchemaInfo([TableInfo("huge", 1, cols)])
    text = schema_to_prompt(schema, max_chars=1000)
    assert len(text) <= 1000
    assert text.endswith("... schema text cut at 1000 characters\n")
