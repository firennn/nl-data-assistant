"""Get the raw Olist CSV files into data/raw/.

Usage: python -m data.download

If the CSVs are already in data/raw/ nothing is downloaded. Otherwise the public Kaggle
dataset is downloaded with kagglehub (no Kaggle account needed for this dataset) and the CSVs
are copied into data/raw/. As a manual alternative, download the zip from
https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce and extract it into data/raw/.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

KAGGLE_DATASET = "olistbr/brazilian-ecommerce"
RAW_DIR = Path(__file__).resolve().parent / "raw"

RAW_FILES = {
    "customers": "olist_customers_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "products": "olist_products_dataset.csv",
    "category_translation": "product_category_name_translation.csv",
    "orders": "olist_orders_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "order_payments": "olist_order_payments_dataset.csv",
    "order_reviews": "olist_order_reviews_dataset.csv",
    "geolocation": "olist_geolocation_dataset.csv",
}


def missing_files(raw_dir: Path) -> list[str]:
    return [name for name in RAW_FILES.values() if not (raw_dir / name).exists()]


def ensure_raw_data(raw_dir: Path = RAW_DIR) -> Path:
    """Make sure all raw CSVs exist in raw_dir, downloading them if needed. Returns raw_dir."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    if not missing_files(raw_dir):
        return raw_dir

    try:
        import kagglehub
    except ImportError as exc:
        raise RuntimeError(
            "kagglehub is not installed (pip install -r requirements.txt), or place the CSV "
            f"files manually in {raw_dir}"
        ) from exc

    print(f"Downloading {KAGGLE_DATASET} ...")
    source = Path(kagglehub.dataset_download(KAGGLE_DATASET))
    for name in RAW_FILES.values():
        if (source / name).exists():
            shutil.copy2(source / name, raw_dir / name)

    still_missing = missing_files(raw_dir)
    if still_missing:
        raise RuntimeError(f"Download incomplete, missing: {', '.join(still_missing)}")
    return raw_dir


def main() -> int:
    path = ensure_raw_data()
    print(f"Raw data ready in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
