"""Build the SQLite database from the raw Olist CSV files.

Usage:
    python -m data.build_db                 # download if needed, then build data/olist.db
    python -m data.build_db --db-path x.db  # custom output path
    python -m data.build_db --no-download   # only use CSVs already in data/raw/

The build is reproducible: the same raw files always produce the same database. The database
is written to a temporary file first and only replaces the old one if every check passes.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import unicodedata
from pathlib import Path

import pandas as pd

from data.download import RAW_DIR, RAW_FILES, ensure_raw_data
from data.schema_docs import SCHEMA_DOCS
from shared.config import get_settings

SCHEMA_SQL = Path(__file__).resolve().parent / "schema.sql"

# Tables in foreign-key order (parents first).
TABLE_ORDER = [
    "customers",
    "sellers",
    "products",
    "orders",
    "order_items",
    "order_payments",
    "order_reviews",
    "geolocation",
]

# Two source categories have no English name in the translation file.
EXTRA_CATEGORY_TRANSLATIONS = {
    "pc_gamer": "pc_gamer",
    "portateis_cozinha_e_preparadores_de_alimentos": "portable_kitchen_food_processors",
}

# Rough bounding box of Brazil, used to drop wrong coordinates.
BRAZIL_LAT = (-33.8, 5.3)
BRAZIL_LNG = (-74.0, -34.8)

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


# --- helpers -------------------------------------------------------------------------------


def normalize_text(series: pd.Series) -> pd.Series:
    """Trim, lowercase and remove accents (e.g. 'São Paulo ' -> 'sao paulo')."""

    def fix(value: object) -> object:
        if not isinstance(value, str):
            return value
        value = unicodedata.normalize("NFKD", value.strip().lower())
        return "".join(ch for ch in value if not unicodedata.combining(ch)) or None

    return series.map(fix)


def to_timestamp_text(series: pd.Series) -> pd.Series:
    """Parse timestamps and return them as 'YYYY-MM-DD HH:MM:SS' text (None if missing)."""
    parsed = pd.to_datetime(series, errors="coerce", format="mixed")
    return parsed.dt.strftime(TIMESTAMP_FORMAT).where(parsed.notna(), None)


def to_int(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").astype("Int64")


def to_float(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


# --- cleaning, one function per table ------------------------------------------------------


def clean_customers(raw: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "customer_id": raw["customer_id"],
            "customer_unique_id": raw["customer_unique_id"],
            "zip_code_prefix": raw["customer_zip_code_prefix"].str.zfill(5),
            "city": normalize_text(raw["customer_city"]),
            "state": raw["customer_state"].str.strip().str.upper(),
        }
    ).drop_duplicates("customer_id")


def clean_sellers(raw: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "seller_id": raw["seller_id"],
            "zip_code_prefix": raw["seller_zip_code_prefix"].str.zfill(5),
            "city": normalize_text(raw["seller_city"]),
            "state": raw["seller_state"].str.strip().str.upper(),
        }
    ).drop_duplicates("seller_id")


def clean_products(raw: pd.DataFrame, translation: pd.DataFrame) -> pd.DataFrame:
    names = dict(
        zip(
            translation["product_category_name"],
            translation["product_category_name_english"],
            strict=True,
        )
    )
    names.update(EXTRA_CATEGORY_TRANSLATIONS)
    category_pt = raw["product_category_name"].str.strip()
    return pd.DataFrame(
        {
            "product_id": raw["product_id"],
            "category": category_pt.map(names),
            "category_pt": category_pt,
            # The source spells these columns "lenght".
            "name_length": to_int(raw["product_name_lenght"]),
            "description_length": to_int(raw["product_description_lenght"]),
            "photos_qty": to_int(raw["product_photos_qty"]),
            "weight_g": to_float(raw["product_weight_g"]),
            "length_cm": to_float(raw["product_length_cm"]),
            "height_cm": to_float(raw["product_height_cm"]),
            "width_cm": to_float(raw["product_width_cm"]),
        }
    ).drop_duplicates("product_id")


def clean_orders(raw: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": raw["order_id"],
            "customer_id": raw["customer_id"],
            "status": raw["order_status"].str.strip().str.lower(),
            "purchase_ts": to_timestamp_text(raw["order_purchase_timestamp"]),
            "approved_ts": to_timestamp_text(raw["order_approved_at"]),
            "delivered_carrier_ts": to_timestamp_text(raw["order_delivered_carrier_date"]),
            "delivered_customer_ts": to_timestamp_text(raw["order_delivered_customer_date"]),
            "estimated_delivery_date": to_timestamp_text(raw["order_estimated_delivery_date"]),
        }
    ).drop_duplicates("order_id")


def clean_order_items(raw: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": raw["order_id"],
            "order_item_id": to_int(raw["order_item_id"]),
            "product_id": raw["product_id"],
            "seller_id": raw["seller_id"],
            "shipping_limit_ts": to_timestamp_text(raw["shipping_limit_date"]),
            "price": to_float(raw["price"]),
            "freight_value": to_float(raw["freight_value"]),
        }
    ).drop_duplicates(["order_id", "order_item_id"])


def clean_order_payments(raw: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": raw["order_id"],
            "payment_sequential": to_int(raw["payment_sequential"]),
            "payment_type": raw["payment_type"].str.strip().str.lower(),
            "installments": to_int(raw["payment_installments"]),
            "payment_value": to_float(raw["payment_value"]),
        }
    ).drop_duplicates(["order_id", "payment_sequential"])


def clean_order_reviews(raw: pd.DataFrame) -> pd.DataFrame:
    """Keep scores and dates. Free-text comments are dropped (they may contain personal
    details); only a has_comment flag is kept."""

    def has_text(col: str) -> pd.Series:
        return raw[col].fillna("").astype(str).str.strip() != ""

    return pd.DataFrame(
        {
            "review_id": raw["review_id"],
            "order_id": raw["order_id"],
            "score": to_int(raw["review_score"]),
            "has_comment": (
                has_text("review_comment_title") | has_text("review_comment_message")
            ).astype(int),
            "created_date": to_timestamp_text(raw["review_creation_date"]),
            "answered_ts": to_timestamp_text(raw["review_answer_timestamp"]),
        }
    ).drop_duplicates(["review_id", "order_id"])


def clean_geolocation(raw: pd.DataFrame) -> pd.DataFrame:
    """One row per zip prefix: average coordinates of the points inside Brazil and the most
    common city/state name."""
    df = pd.DataFrame(
        {
            "zip_code_prefix": raw["geolocation_zip_code_prefix"].str.zfill(5),
            "latitude": to_float(raw["geolocation_lat"]),
            "longitude": to_float(raw["geolocation_lng"]),
            "city": normalize_text(raw["geolocation_city"]),
            "state": raw["geolocation_state"].str.strip().str.upper(),
        }
    ).drop_duplicates()
    inside = df["latitude"].between(*BRAZIL_LAT) & df["longitude"].between(*BRAZIL_LNG)
    df = df[inside]

    def most_common(values: pd.Series) -> object:
        counts = values.dropna().value_counts()
        return counts.index[0] if not counts.empty else None

    grouped = df.groupby("zip_code_prefix", sort=True)
    return pd.DataFrame(
        {
            "latitude": grouped["latitude"].mean().round(6),
            "longitude": grouped["longitude"].mean().round(6),
            "city": grouped["city"].agg(most_common),
            "state": grouped["state"].agg(most_common),
        }
    ).reset_index()


# --- build ---------------------------------------------------------------------------------


def load_raw(raw_dir: Path) -> dict[str, pd.DataFrame]:
    """Read every raw CSV as text so no value is changed before cleaning."""
    return {
        key: pd.read_csv(raw_dir / name, dtype=str, keep_default_na=True, encoding="utf-8")
        for key, name in RAW_FILES.items()
    }


def clean_all(raw: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    return {
        "customers": clean_customers(raw["customers"]),
        "sellers": clean_sellers(raw["sellers"]),
        "products": clean_products(raw["products"], raw["category_translation"]),
        "orders": clean_orders(raw["orders"]),
        "order_items": clean_order_items(raw["order_items"]),
        "order_payments": clean_order_payments(raw["order_payments"]),
        "order_reviews": clean_order_reviews(raw["order_reviews"]),
        "geolocation": clean_geolocation(raw["geolocation"]),
    }


def schema_docs_rows() -> list[tuple[str, str, str]]:
    return [
        (table, column, text)
        for table, columns in SCHEMA_DOCS.items()
        for column, text in columns.items()
    ]


def validate(conn: sqlite3.Connection) -> list[str]:
    """Return a list of problems (empty if the database is consistent)."""
    problems = []
    for table in TABLE_ORDER:
        if conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0:
            problems.append(f"table {table} is empty")
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        problems.append(f"{len(violations)} foreign key violations, e.g. {violations[0]}")
    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        problems.append("integrity check failed")
    return problems


def build_database(raw_dir: Path, db_path: Path) -> dict[str, int]:
    """Clean the raw CSVs and write the database. Returns row counts per table.

    Raises RuntimeError if any validation check fails (the old database is kept).
    """
    tables = clean_all(load_raw(raw_dir))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = db_path.with_name(db_path.name + ".tmp")
    tmp_path.unlink(missing_ok=True)

    conn = sqlite3.connect(tmp_path)
    ok = False
    try:
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        for name in TABLE_ORDER:
            try:
                tables[name].to_sql(name, conn, if_exists="append", index=False, chunksize=5000)
            except Exception as exc:  # pandas wraps sqlite errors in its own DatabaseError
                cause = exc.__cause__ or exc
                raise RuntimeError(f"Loading table {name} failed: {cause}") from exc
        conn.executemany(
            "INSERT INTO _schema_docs (table_name, column_name, description) VALUES (?, ?, ?)",
            schema_docs_rows(),
        )
        conn.commit()
        problems = validate(conn)
        counts = {
            name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in TABLE_ORDER
        }
        if problems:
            raise RuntimeError("Database checks failed: " + "; ".join(problems))
        conn.execute("VACUUM")
        ok = True
    finally:
        conn.close()
        if not ok:
            tmp_path.unlink(missing_ok=True)

    tmp_path.replace(db_path)
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Olist SQLite database.")
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--db-path", type=Path, default=None, help="default: DB_PATH from .env")
    parser.add_argument("--no-download", action="store_true", help="only use existing CSVs")
    args = parser.parse_args(argv)

    if not args.no_download:
        ensure_raw_data(args.raw_dir)
    db_path = args.db_path or get_settings().db_path

    counts = build_database(args.raw_dir, db_path)
    width = max(len(t) for t in counts)
    print(f"Built {db_path}")
    for table, n in counts.items():
        print(f"  {table:<{width}}  {n:>9,} rows")
    print(f"  size: {db_path.stat().st_size / 1_048_576:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
