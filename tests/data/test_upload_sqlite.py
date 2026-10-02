"""Uploading a SQLite file (data/upload.py)."""

import hashlib
import io
import json
import sqlite3

import pytest

import data.upload as upload
from agent import SQLAgent
from data.upload import UploadError, build_user_db
from shared.db import run_query
from shared.llm import FakeProvider
from shared.schema import describe_schema


def make_db(path, *statements):
    conn = sqlite3.connect(path)
    try:
        for sql in statements:
            conn.execute(sql)
        conn.commit()
    finally:
        conn.close()
    return path


def shop_db(path):
    return make_db(
        path,
        'CREATE TABLE "Staff" ("Staff ID" INTEGER PRIMARY KEY, "Name" TEXT, "Store" TEXT)',
        'CREATE TABLE "Sales Data" ("Order ID" INTEGER PRIMARY KEY, "Staff ID" INTEGER '
        'REFERENCES "Staff"("Staff ID"), "Amount (€)" REAL, "select" TEXT)',
        'CREATE TABLE "_hidden" (x INTEGER)',
        'CREATE TABLE "order" (id INTEGER)',
        "INSERT INTO \"Staff\" VALUES (1, 'Ana', 'Berlin'), (2, 'Ben', 'Hamburg')",
        "INSERT INTO \"Sales Data\" VALUES (10, 1, 12.5, 'a'), (11, 2, 7.5, 'b'), (12, 1, 5, 'c')",
        'INSERT INTO "_hidden" VALUES (1)',
    )


def test_sqlite_upload_cleans_names_and_keeps_keys(tmp_path):
    up = build_user_db([shop_db(tmp_path / "shop.sqlite")], tmp_path / "out")
    assert up.tables == {
        "hidden": (1, 1), "order_": (0, 1), "sales_data": (3, 4), "staff": (2, 3),
    }  # fmt: skip
    schema = describe_schema(up.db_path)
    sales = schema.table("sales_data")
    assert [c.name for c in sales.columns] == ["order_id", "staff_id", "amount", "select_"]
    assert sales.column("staff_id").fk_ref == "staff.staff_id"  # renames keep the foreign key
    df = run_query(
        "SELECT st.store, SUM(s.amount) AS total FROM sales_data s "
        "JOIN staff st ON st.staff_id = s.staff_id GROUP BY st.store ORDER BY st.store",
        db_path=up.db_path,
    )
    assert df.to_dict("records") == [
        {"store": "Berlin", "total": 17.5}, {"store": "Hamburg", "total": 7.5},
    ]  # fmt: skip
    notes = " ".join(up.notes)
    assert "renamed tables" in notes and "_hidden -> hidden" in notes and "order -> order_" in notes
    assert "Sales Data -> sales_data" in notes
    assert "Staff -> staff" not in notes  # case-only changes are not listed
    assert "'Amount (€)' -> amount" in notes and "'select' -> select_" in notes


def test_sqlite_profile(tmp_path):
    up = build_user_db([shop_db(tmp_path / "shop.db")], tmp_path)
    p = up.profile
    assert p.description == "data uploaded by the user (tables: hidden, order_, sales_data, staff)"
    assert p.rules == [] and p.date_range is None
    assert p.include_samples is False


def test_original_file_is_not_changed(tmp_path):
    src = shop_db(tmp_path / "shop.sqlite3")
    before = hashlib.sha256(src.read_bytes()).hexdigest()
    up = build_user_db([src], tmp_path / "out")
    assert hashlib.sha256(src.read_bytes()).hexdigest() == before
    assert up.db_path != src


def test_file_object_source_and_agent_end_to_end(tmp_path):
    src = shop_db(tmp_path / "shop.db")
    f = io.BytesIO(src.read_bytes())
    f.name = "upload.db"
    up = build_user_db([f], tmp_path / "out")
    reply = json.dumps({"action": "sql", "sql": "SELECT COUNT(*) AS n FROM sales_data"})
    llm = FakeProvider([reply, "There are 3 sales."])
    result = SQLAgent(llm=llm, db_path=up.db_path, profile=up.profile).ask("How many sales?")
    assert result.ok and result.data["n"].iloc[0] == 3
    prompt, _ = llm.calls[0]
    assert "TABLE sales_data" in prompt and "samples:" not in prompt


def test_wal_mode_file_becomes_a_single_file(tmp_path):
    src = make_db(tmp_path / "wal.db", "PRAGMA journal_mode = WAL", "CREATE TABLE t (x)")
    up = build_user_db([src], tmp_path / "out")
    conn = sqlite3.connect(up.db_path)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        conn.close()


# --- rejected files ------------------------------------------------------------------------


def test_not_a_sqlite_file(tmp_path):
    fake = tmp_path / "data.db"
    fake.write_text("name,age\nana,31\n")
    with pytest.raises(UploadError, match=r"^data.db is not a SQLite database\.$"):
        build_user_db([fake], tmp_path / "out")


def test_damaged_file(tmp_path):
    src = make_db(
        tmp_path / "broken.db",
        "CREATE TABLE t (x TEXT)",
        "INSERT INTO t SELECT hex(randomblob(500)) FROM "
        "(WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 200) "
        "SELECT i FROM n)",
    )
    data = bytearray(src.read_bytes())
    data[4096 + 100 : 4096 + 600] = b"\xff" * 500  # overwrite part of the second page
    src.write_bytes(bytes(data))
    with pytest.raises(UploadError, match=r"^broken.db is damaged and cannot be used"):
        build_user_db([src], tmp_path / "out")


@pytest.mark.parametrize(
    "statement, found",
    [
        ("CREATE VIEW big_sales AS SELECT * FROM t WHERE x > 1", "big_sales"),
        ("CREATE TRIGGER log AFTER INSERT ON t BEGIN SELECT 1; END", "log"),
    ],
)
def test_views_and_triggers_are_rejected(tmp_path, statement, found):
    src = make_db(tmp_path / "v.db", "CREATE TABLE t (x)", statement)
    with pytest.raises(UploadError, match=rf"contains views or triggers \({found}\)"):
        build_user_db([src], tmp_path / "out")


def test_virtual_tables_are_rejected(tmp_path):
    src = make_db(tmp_path / "fts.db", "CREATE VIRTUAL TABLE docs USING fts5(body)")
    with pytest.raises(UploadError, match=r"contains virtual tables \(docs\)"):
        build_user_db([src], tmp_path / "out")


def test_file_without_tables(tmp_path):
    src = make_db(tmp_path / "empty.db", "CREATE TABLE t (x)", "DROP TABLE t")
    with pytest.raises(UploadError, match=r"^empty.db has no tables\.$"):
        build_user_db([src], tmp_path / "out")


def test_sqlite_must_be_uploaded_alone(tmp_path):
    src = shop_db(tmp_path / "shop.db")
    extra = tmp_path / "x.csv"
    extra.write_text("a\n1\n")
    with pytest.raises(UploadError, match="Upload one SQLite file on its own"):
        build_user_db([src, extra], tmp_path / "out")


def test_sqlite_limits(tmp_path, monkeypatch):
    src = shop_db(tmp_path / "shop.db")
    monkeypatch.setattr(upload, "MAX_TABLES", 3)
    with pytest.raises(UploadError, match=r"^shop.db has 4 tables; the limit is 3\.$"):
        build_user_db([src], tmp_path / "out")
    monkeypatch.setattr(upload, "MAX_TABLES", 100)

    monkeypatch.setattr(upload, "MAX_COLUMNS", 3)
    with pytest.raises(UploadError, match=r"table sales_data has 4 columns; the limit is 3\."):
        build_user_db([src], tmp_path / "out")
    monkeypatch.setattr(upload, "MAX_COLUMNS", 200)

    monkeypatch.setattr(upload, "MAX_ROWS", 2)
    with pytest.raises(UploadError, match=r"table sales_data has 3 rows; the limit is 2\."):
        build_user_db([src], tmp_path / "out")


def test_failed_sqlite_upload_leaves_no_files(tmp_path):
    src = make_db(tmp_path / "v.db", "CREATE TABLE t (x)", "CREATE VIEW v AS SELECT * FROM t")
    out = tmp_path / "out"
    with pytest.raises(UploadError):
        build_user_db([src], out)
    assert list(out.iterdir()) == []
