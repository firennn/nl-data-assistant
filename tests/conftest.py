"""Shared test fixtures: a tiny database with the real schema and a few hand-made rows."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

SCHEMA_SQL = Path(__file__).resolve().parent.parent / "data" / "schema.sql"

ROWS = {
    "customers": [
        ("c1", "u1", "01001", "sao paulo", "SP"),
        ("c2", "u2", "20040", "rio de janeiro", "RJ"),
        ("c3", "u1", "01001", "sao paulo", "SP"),
    ],
    "sellers": [("s1", "13000", "campinas", "SP"), ("s2", "30100", "belo horizonte", "MG")],
    "products": [
        ("p1", "bed_bath_table", "cama_mesa_banho", 40, 300, 2, 900.0, 30.0, 10.0, 20.0),
        ("p2", "health_beauty", "beleza_saude", 35, 500, 1, 200.0, 15.0, 5.0, 10.0),
        ("p3", "sports_leisure", "esporte_lazer", 50, 800, 4, 1500.0, 40.0, 20.0, 30.0),
    ],
    "orders": [
        ("o1", "c1", "delivered", "2018-01-02 10:00:00", "2018-01-02 11:00:00",
         "2018-01-03 09:00:00", "2018-01-08 15:00:00", "2018-01-15 00:00:00"),
        ("o2", "c2", "delivered", "2018-01-05 14:30:00", "2018-01-05 15:00:00",
         "2018-01-06 10:00:00", "2018-01-20 12:00:00", "2018-01-18 00:00:00"),
        ("o3", "c3", "canceled", "2018-01-09 08:15:00", None, None, None, "2018-01-25 00:00:00"),
    ],
    "order_items": [
        ("o1", 1, "p1", "s1", "2018-01-04 10:00:00", 120.0, 15.5),
        ("o1", 2, "p2", "s2", "2018-01-04 10:00:00", 35.9, 8.0),
        ("o2", 1, "p3", "s1", "2018-01-07 14:30:00", 249.9, 22.1),
        ("o3", 1, "p2", "s2", "2018-01-11 08:15:00", 35.9, 8.0),
    ],
    "order_payments": [
        ("o1", 1, "credit_card", 3, 179.4),
        ("o2", 1, "boleto", 1, 272.0),
        ("o3", 1, "voucher", 1, 43.9),
    ],
    "order_reviews": [
        ("r1", "o1", 5, 1, "2018-01-09 00:00:00", "2018-01-10 10:00:00"),
        ("r2", "o2", 2, 0, "2018-01-21 00:00:00", "2018-01-22 09:00:00"),
    ],
    "geolocation": [
        ("01001", -23.55, -46.63, "sao paulo", "SP"),
        ("20040", -22.90, -43.18, "rio de janeiro", "RJ"),
    ],
    "_schema_docs": [
        ("orders", "", "One row per order."),
        ("orders", "status", "Order status, e.g. delivered, shipped, canceled."),
        ("order_items", "price", "Item price in BRL, excluding freight."),
    ],
}  # fmt: skip


def build_fixture_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        for table, rows in ROWS.items():
            placeholders = ", ".join("?" * len(rows[0]))
            conn.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)
        conn.commit()
    finally:
        conn.close()
    return path


@pytest.fixture
def sample_db(tmp_path: Path) -> Path:
    """Path to a fresh fixture database file."""
    return build_fixture_db(tmp_path / "test.db")
