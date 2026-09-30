"""Tests for the cleaning functions and the database build, using tiny raw CSV files with the
same columns as the Olist source."""

import pandas as pd
import pytest

from data import build_db
from data.download import RAW_FILES, missing_files
from shared.db import run_query
from shared.schema import describe_schema

RAW = {
    "customers": {
        "customer_id": ["c1", "c2"],
        "customer_unique_id": ["u1", "u1"],
        "customer_zip_code_prefix": ["1001", "20040"],  # leading zero lost in source
        "customer_city": [" São Paulo ", "rio de janeiro"],
        "customer_state": ["sp", "RJ"],
    },
    "sellers": {
        "seller_id": ["s1"],
        "seller_zip_code_prefix": ["13000"],
        "seller_city": ["Campinas"],
        "seller_state": ["SP"],
    },
    "products": {
        "product_id": ["p1", "p2", "p3"],
        "product_category_name": ["beleza_saude", "pc_gamer", None],
        "product_name_lenght": ["40", "30", None],
        "product_description_lenght": ["300", "200", None],
        "product_photos_qty": ["2", "1", None],
        "product_weight_g": ["900", "1500", "100"],
        "product_length_cm": ["30", "40", "10"],
        "product_height_cm": ["10", "20", "5"],
        "product_width_cm": ["20", "30", "5"],
    },
    "category_translation": {
        "product_category_name": ["beleza_saude"],
        "product_category_name_english": ["health_beauty"],
    },
    "orders": {
        "order_id": ["o1", "o2"],
        "customer_id": ["c1", "c2"],
        "order_status": ["delivered", "Canceled"],
        "order_purchase_timestamp": ["2018-01-02 10:00:00", "2018-01-05 14:30:00"],
        "order_approved_at": ["2018-01-02 11:00:00", None],
        "order_delivered_carrier_date": ["2018-01-03 09:00:00", None],
        "order_delivered_customer_date": ["2018-01-08 15:00:00", None],
        "order_estimated_delivery_date": ["2018-01-15 00:00:00", "2018-01-20 00:00:00"],
    },
    "order_items": {
        "order_id": ["o1", "o1", "o2"],
        "order_item_id": ["1", "2", "1"],
        "product_id": ["p1", "p2", "p3"],
        "seller_id": ["s1", "s1", "s1"],
        "shipping_limit_date": ["2018-01-04 10:00:00"] * 3,
        "price": ["120.00", "35.90", "10.00"],
        "freight_value": ["15.50", "8.00", "5.00"],
    },
    "order_payments": {
        "order_id": ["o1", "o2"],
        "payment_sequential": ["1", "1"],
        "payment_type": ["credit_card", "boleto"],
        "payment_installments": ["3", "1"],
        "payment_value": ["179.40", "15.00"],
    },
    "order_reviews": {
        "review_id": ["r1", "r2", "r2"],
        "order_id": ["o1", "o2", "o1"],  # same review id on two orders (happens in source)
        "review_score": ["5", "1", "4"],
        "review_comment_title": [None, None, "  "],
        "review_comment_message": ["Recebi rápido, Maria", None, None],
        "review_creation_date": ["2018-01-09 00:00:00"] * 3,
        "review_answer_timestamp": ["2018-01-10 10:00:00"] * 3,
    },
    "geolocation": {
        "geolocation_zip_code_prefix": ["01001", "01001", "01001", "20040", "99999"],
        "geolocation_lat": ["-23.50", "-23.60", "-23.60", "-22.90", "40.0"],
        "geolocation_lng": ["-46.60", "-46.70", "-46.70", "-43.18", "-3.0"],
        "geolocation_city": ["são paulo", "sao paulo", "sao paulo", "rio de janeiro", "madrid"],
        "geolocation_state": ["SP", "SP", "SP", "RJ", "XX"],
    },
}


@pytest.fixture
def raw_dir(tmp_path):
    directory = tmp_path / "raw"
    directory.mkdir()
    for key, columns in RAW.items():
        pd.DataFrame(columns).to_csv(directory / RAW_FILES[key], index=False)
    return directory


@pytest.fixture
def built_db(raw_dir, tmp_path):
    db_path = tmp_path / "olist.db"
    counts = build_db.build_database(raw_dir, db_path)
    return db_path, counts


def test_all_raw_files_present(raw_dir):
    assert missing_files(raw_dir) == []


def test_normalize_text():
    result = build_db.normalize_text(pd.Series([" São Paulo ", "RIO", None, "  "]))
    assert result.tolist()[:2] == ["sao paulo", "rio"]
    assert result.iloc[2:].isna().all()


def test_clean_customers_restores_zip_leading_zeros():
    df = build_db.clean_customers(pd.DataFrame(RAW["customers"]))
    assert df["zip_code_prefix"].tolist() == ["01001", "20040"]
    assert df["city"].tolist() == ["sao paulo", "rio de janeiro"]
    assert df["state"].tolist() == ["SP", "RJ"]


def test_clean_products_translates_categories_and_renames_columns():
    df = build_db.clean_products(
        pd.DataFrame(RAW["products"]), pd.DataFrame(RAW["category_translation"])
    )
    assert df["category"].tolist()[:2] == ["health_beauty", "pc_gamer"]
    assert pd.isna(df["category"].iloc[2])
    assert "name_length" in df.columns and "product_name_lenght" not in df.columns


def test_clean_reviews_drops_text_and_keeps_flag():
    df = build_db.clean_order_reviews(pd.DataFrame(RAW["order_reviews"]))
    assert "review_comment_message" not in df.columns
    assert df["has_comment"].tolist() == [1, 0, 0]  # whitespace-only title is not a comment
    assert len(df) == 3  # (review_id, order_id) pairs are unique


def test_clean_geolocation_aggregates_and_drops_outside_brazil():
    df = build_db.clean_geolocation(pd.DataFrame(RAW["geolocation"])).set_index("zip_code_prefix")
    assert list(df.index) == ["01001", "20040"]  # 99999 (Madrid) dropped
    assert df.loc["01001", "latitude"] == pytest.approx(-23.55)  # exact duplicate counted once
    assert df.loc["01001", "city"] == "sao paulo"


def test_build_database_counts_and_types(built_db):
    db_path, counts = built_db
    assert counts == {
        "customers": 2,
        "sellers": 1,
        "products": 3,
        "orders": 2,
        "order_items": 3,
        "order_payments": 2,
        "order_reviews": 3,
        "geolocation": 2,
    }
    df = run_query("SELECT status, purchase_ts FROM orders ORDER BY order_id", db_path=db_path)
    assert df["status"].tolist() == ["delivered", "canceled"]
    revenue = run_query(
        "SELECT SUM(price) AS revenue FROM order_items JOIN orders USING (order_id) "
        "WHERE status NOT IN ('canceled', 'unavailable')",
        db_path=db_path,
    )
    assert revenue["revenue"].iloc[0] == pytest.approx(155.90)


def test_build_database_writes_schema_docs(built_db):
    db_path, _ = built_db
    orders = describe_schema(db_path).table("orders")
    assert orders.description.startswith("One row per order")
    assert "delivered" in orders.column("status").description


def test_build_database_enforces_foreign_keys(raw_dir, tmp_path):
    items = pd.read_csv(raw_dir / RAW_FILES["order_items"], dtype=str)
    items.loc[0, "product_id"] = "missing-product"
    items.to_csv(raw_dir / RAW_FILES["order_items"], index=False)
    with pytest.raises(RuntimeError, match="order_items.*FOREIGN KEY"):
        build_db.build_database(raw_dir, tmp_path / "bad.db")
    assert not (tmp_path / "bad.db").exists()
    assert not (tmp_path / "bad.db.tmp").exists()


def test_build_keeps_old_database_on_failure(raw_dir, tmp_path, monkeypatch):
    db_path = tmp_path / "olist.db"
    build_db.build_database(raw_dir, db_path)
    before = db_path.stat().st_size
    monkeypatch.setattr(build_db, "validate", lambda conn: ["simulated problem"])
    with pytest.raises(RuntimeError, match="simulated problem"):
        build_db.build_database(raw_dir, db_path)
    assert db_path.stat().st_size == before
