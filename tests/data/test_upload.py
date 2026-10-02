"""Building a database from uploaded CSV files (data/upload.py)."""

import io
import sqlite3

import pytest

import data.upload as upload
from data.upload import UploadError, build_user_db, clean_identifier
from shared.db import run_query
from shared.schema import describe_schema


def csv_file(name: str, text: str, encoding: str = "utf-8") -> io.BytesIO:
    f = io.BytesIO(text.encode(encoding))
    f.name = name
    return f


def rows(up, table):
    return run_query(f'SELECT * FROM "{table}"', db_path=up.db_path)


def column_types(up, table):
    with sqlite3.connect(up.db_path) as conn:
        types = {r[1]: r[2] for r in conn.execute(f'PRAGMA table_info("{table}")')}
    conn.close()
    return types


# --- names ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (" Order ID", "order_id"),
        ("Total (€)", "total"),
        ("São Paulo", "sao_paulo"),
        ("2023", "c_2023"),
        ("_hidden", "hidden"),
        ("__", "fallback"),
        ("select", "select_"),
        ("Order", "order_"),
        ("group by", "group_by"),
    ],
)
def test_clean_identifier(raw, expected):
    assert clean_identifier(raw, "fallback") == expected


def test_messy_headers_are_cleaned_and_reported(tmp_path):
    f = csv_file(
        "messy.csv",
        "﻿ Order ID,Total (€),2023,a,A,,select,_hidden\n1,2,3,4,5,6,7,8\n",
    )
    up = build_user_db([f], tmp_path)
    assert list(rows(up, "messy").columns) == [
        "order_id", "total", "c_2023", "a", "a_2", "column_6", "select_", "hidden",
    ]  # fmt: skip
    renames = next(n for n in up.notes if "renamed columns" in n)
    assert "' Order ID' -> order_id" in renames and "'' -> column_6" in renames
    assert "'a'" not in renames  # unchanged names are not listed


@pytest.mark.parametrize(
    "file_name, table",
    [
        ("Sales 2023.csv", "sales_2023"),
        ("2023 sales.csv", "t_2023_sales"),
        ("_private.csv", "private"),
        ("order.csv", "order_"),
        ("€.csv", "table"),
    ],
)
def test_table_names_follow_the_same_rules(tmp_path, file_name, table):
    up = build_user_db([csv_file(file_name, "x\n1\n")], tmp_path)
    assert list(up.tables) == [table]
    assert table in describe_schema(up.db_path).table_names  # never hidden as internal


def test_duplicate_table_names_get_a_suffix(tmp_path):
    up = build_user_db([csv_file("Sales.csv", "x\n1\n"), csv_file("sales.csv", "y\n2\n")], tmp_path)
    assert list(up.tables) == ["sales", "sales_2"]
    assert any("stored as table sales_2" in n for n in up.notes)


# --- encodings and delimiters --------------------------------------------------------------


def test_semicolon_windows_1252_decimal_comma_and_day_first_dates(tmp_path):
    text = "Datum;Kunde;Betrag;Menge\n25/03/2023;Müller;1.234,50;3\n02/04/2023;Ana;12,5;1\n"
    up = build_user_db([csv_file("verkauf.csv", text, "cp1252")], tmp_path)
    df = rows(up, "verkauf")
    assert df["datum"].tolist() == ["2023-03-25", "2023-04-02"]
    assert df["kunde"].tolist() == ["Müller", "Ana"]
    assert df["betrag"].tolist() == [1234.5, 12.5]
    assert column_types(up, "verkauf") == {
        "datum": "TEXT", "kunde": "TEXT", "betrag": "REAL", "menge": "INTEGER",
    }  # fmt: skip
    notes = " ".join(up.notes)
    assert "read as Windows-1252" in notes
    assert "separated by ';'" in notes
    assert "betrag uses decimal commas" in notes
    assert "datum were read as day/month" in notes


def test_tab_separated_file(tmp_path):
    up = build_user_db([csv_file("t.csv", "a\tb\n1\t2\n")], tmp_path)
    assert rows(up, "t").to_dict("records") == [{"a": 1, "b": 2}]
    assert any("separated by tab" in n for n in up.notes)


def test_utf8_with_byte_order_mark_needs_no_note(tmp_path):
    up = build_user_db([csv_file("u.csv", "﻿name\nJosé\n")], tmp_path)
    assert rows(up, "u")["name"].tolist() == ["José"]
    assert not any("UTF-8" in n for n in up.notes)


def test_quoted_values_with_delimiters_inside(tmp_path):
    up = build_user_db([csv_file("q.csv", 'name,amount\n"Smith, Ann","1,234.50"\n')], tmp_path)
    assert rows(up, "q").to_dict("records") == [{"name": "Smith, Ann", "amount": 1234.5}]


# --- types ---------------------------------------------------------------------------------


def test_strict_type_inference(tmp_path):
    text = (
        "int_col,real_col,mixed,zip,empty,sparse\n"
        "1,1.5,12,01001,,NA\n"
        "2,2,abc,20040,,null\n"
        "-3,1e3,7,30100,,5\n"
    )
    up = build_user_db([csv_file("types.csv", text)], tmp_path)
    assert column_types(up, "types") == {
        "int_col": "INTEGER", "real_col": "REAL", "mixed": "TEXT",
        "zip": "TEXT", "empty": "TEXT", "sparse": "INTEGER",
    }  # fmt: skip
    df = rows(up, "types")
    assert df["zip"].tolist() == ["01001", "20040", "30100"]  # leading zeros kept
    assert df["empty"].isna().all()
    assert df["sparse"].tolist()[2] == 5 and df["sparse"].isna().sum() == 2
    notes = " ".join(up.notes)
    assert "zip is kept as text because of leading zeros" in notes
    assert "column empty is empty" in notes


def test_huge_integers_stay_text(tmp_path):
    up = build_user_db([csv_file("big.csv", "n\n123456789012345678901234567890\n")], tmp_path)
    assert column_types(up, "big") == {"n": "TEXT"}


def test_point_decimals_in_a_semicolon_file_stay_decimals(tmp_path):
    up = build_user_db([csv_file("s.csv", "w;v\n1.500;2\n2.250;3\n")], tmp_path)
    assert rows(up, "s")["w"].tolist() == [1.5, 2.25]


def test_dates_and_timestamps(tmp_path):
    text = (
        "iso,stamp,us,two_digit,not_a_date\n"
        "2023-01-05,2023-01-05 08:30:00,03/25/2023,25/03/23,2023-13-45\n"
        "2023-02-10,2023-02-10T17:05,04/01/2023,01/04/23,2023-01-01\n"
    )
    up = build_user_db([csv_file("dates.csv", text)], tmp_path)
    df = rows(up, "dates")
    assert df["iso"].tolist() == ["2023-01-05", "2023-02-10"]
    assert df["stamp"].tolist() == ["2023-01-05 08:30:00", "2023-02-10 17:05:00"]
    assert df["us"].tolist() == ["2023-03-25", "2023-04-01"]  # second part above 12
    assert df["two_digit"].tolist() == ["2023-03-25", "2023-04-01"]
    assert df["not_a_date"].tolist() == ["2023-13-45", "2023-01-01"]  # invalid -> text
    notes = " ".join(up.notes)
    assert "us were read as month/day" in notes
    assert "not_a_date" not in notes  # no misleading date note for a text column


def test_ambiguous_dates_default_to_day_first_with_a_note(tmp_path):
    text = "d\n03/04/2023\n05/06/2023\n"
    up = build_user_db([csv_file("a.csv", text)], tmp_path)
    assert rows(up, "a")["d"].tolist() == ["2023-04-03", "2023-06-05"]
    assert any("ambiguous; read as day/month (the default)" in n for n in up.notes)

    up = build_user_db([csv_file("a.csv", text)], tmp_path, dayfirst=False)
    assert rows(up, "a")["d"].tolist() == ["2023-03-04", "2023-05-06"]
    assert any("ambiguous; read as month/day (as requested)" in n for n in up.notes)

    up = build_user_db([csv_file("a.csv", text)], tmp_path, dayfirst=True)
    assert any("ambiguous; read as day/month (as requested)" in n for n in up.notes)


def test_clear_day_first_dates_win_over_the_argument(tmp_path):
    up = build_user_db([csv_file("d.csv", "d\n25/03/2023\n")], tmp_path, dayfirst=False)
    assert rows(up, "d")["d"].tolist() == ["2023-03-25"]


# --- profile and result --------------------------------------------------------------------


def test_profile_and_result(tmp_path):
    sales = csv_file("sales.csv", "sale_date,amount\n2023-01-05,10\n2023-06-30,20\n2023-03-01,5\n")
    staff = csv_file("staff.csv", "name,started\nAna,2020-02-01\n")
    up = build_user_db([sales, staff], tmp_path)
    assert up.tables == {"sales": (3, 2), "staff": (1, 2)}
    assert up.db_path.parent == tmp_path and up.db_path.suffix == ".db"
    p = up.profile
    assert p.description == "data uploaded by the user (tables: sales, staff)"
    assert p.rules == [
        "Dates in sales.sale_date run from 2023-01-05 to 2023-06-30.",
        "Dates in staff.started run from 2020-02-01 to 2020-02-01.",
    ]
    assert p.date_range is None  # completeness of user data is unknown
    assert p.include_samples is False and p.currency is None


def test_database_is_new_each_time_and_readable(tmp_path):
    a = build_user_db([csv_file("x.csv", "v\n1\n")], tmp_path)
    b = build_user_db([csv_file("x.csv", "v\n1\n")], tmp_path)
    assert a.db_path != b.db_path
    assert describe_schema(a.db_path).table("x").row_count == 1


def test_paths_work_as_sources(tmp_path):
    path = tmp_path / "from disk.csv"
    path.write_text("v\n1\n2\n", encoding="utf-8")
    up = build_user_db([path], tmp_path / "out")
    assert up.tables == {"from_disk": (2, 1)}


def test_header_only_file(tmp_path):
    up = build_user_db([csv_file("h.csv", "a,b\n")], tmp_path)
    assert up.tables == {"h": (0, 2)}
    assert any("no data rows" in n for n in up.notes)


def test_short_rows_are_padded_with_nulls(tmp_path):
    up = build_user_db([csv_file("p.csv", "a,b\n1\n2,3\n")], tmp_path)
    assert rows(up, "p")["b"].isna().tolist() == [True, False]


# --- errors and limits ---------------------------------------------------------------------


def test_errors(tmp_path):
    with pytest.raises(UploadError, match="No files were uploaded"):
        build_user_db([], tmp_path)
    with pytest.raises(UploadError, match=r"^report.xlsx is not a CSV or SQLite file\. Only \.csv"):
        build_user_db([csv_file("report.xlsx", "x")], tmp_path)
    with pytest.raises(UploadError, match=r"^e.csv is empty\.$"):
        build_user_db([csv_file("e.csv", "  \n\n")], tmp_path)
    with pytest.raises(UploadError, match="row 3 has 3 values but the header has 2"):
        build_user_db([csv_file("w.csv", "a,b\n1,2\n1,2,3\n")], tmp_path)
    nameless = io.BytesIO(b"a\n1\n")
    with pytest.raises(UploadError, match="needs a name"):
        build_user_db([nameless], tmp_path)


def test_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(upload, "MAX_FILES", 2)
    with pytest.raises(UploadError, match=r"^Too many files: 3 \(the limit is 2\)\.$"):
        build_user_db([csv_file(f"{i}.csv", "a\n1\n") for i in range(3)], tmp_path)

    monkeypatch.setattr(upload, "MAX_ROWS", 2)
    with pytest.raises(UploadError, match=r"^r.csv has 3 rows; the limit is 2\.$"):
        build_user_db([csv_file("r.csv", "a\n1\n2\n3\n")], tmp_path)

    monkeypatch.setattr(upload, "MAX_COLUMNS", 2)
    with pytest.raises(UploadError, match=r"^c.csv has 3 columns; the limit is 2\.$"):
        build_user_db([csv_file("c.csv", "a,b,c\n1,2,3\n")], tmp_path)


def test_size_limit_is_checked_before_reading(tmp_path, monkeypatch):
    monkeypatch.setattr(upload, "MAX_UPLOAD_BYTES", 1024 * 1024)

    class Unreadable(io.BytesIO):
        name = "big.csv"
        size = 3 * 1024 * 1024

        def read(self, *args):
            raise AssertionError("the file must not be read")

    with pytest.raises(UploadError, match=r"^The upload is 3\.0 MB; the limit is 1 MB\.$"):
        build_user_db([Unreadable()], tmp_path)


def test_size_of_a_file_object_without_size_attribute(tmp_path, monkeypatch):
    monkeypatch.setattr(upload, "MAX_UPLOAD_BYTES", 10)
    with pytest.raises(UploadError, match="the limit is"):
        build_user_db([csv_file("s.csv", "a\n" + "1\n" * 20)], tmp_path)


def test_failed_build_leaves_no_files(tmp_path):
    with pytest.raises(UploadError):
        build_user_db([csv_file("ok.csv", "a\n1\n"), csv_file("bad.csv", "a\n1,2\n")], tmp_path)
    assert list(tmp_path.iterdir()) == []
