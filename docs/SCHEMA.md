# Database schema

SQLite database built from the [Olist Brazilian E-Commerce dataset](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)
(CC BY-NC-SA 4.0). Definition: `data/schema.sql`. Column descriptions: `data/schema_docs.py`
(stored in the `_schema_docs` table and used in the schema text for SQL generation).

## Rebuild

```bash
python -m data.build_db              # downloads the CSVs to data/raw/ if needed (no account required)
python -m data.build_db --no-download   # use CSVs already in data/raw/
```

The build takes under a minute and produces `data/olist.db` (about 115 MB, not committed).
It writes to a temporary file and only replaces the existing database if all checks pass
(no empty tables, `PRAGMA foreign_key_check`, `PRAGMA integrity_check`).

## Relationships

```
customers 1---n orders 1---n order_items n---1 products
                  |    \          |
                  |     \         n---1 sellers
                  |      1---n order_payments
                  1---n order_reviews

customers.zip_code_prefix / sellers.zip_code_prefix  ...>  geolocation.zip_code_prefix  (soft join, no FK)
```

## Tables

| Table | Rows | Key | Description |
|---|---:|---|---|
| `customers` | 99,441 | `customer_id` | One row per order-customer pair; `customer_unique_id` identifies the person (96,096 people). |
| `sellers` | 3,095 | `seller_id` | Marketplace sellers with location. |
| `products` | 32,951 | `product_id` | Category (English and Portuguese), size and weight. |
| `orders` | 99,441 | `order_id` | Status and lifecycle timestamps (purchase, approval, carrier, delivery, estimate). |
| `order_items` | 112,650 | `order_id, order_item_id` | One row per item: product, seller, price, freight. |
| `order_payments` | 103,886 | `order_id, payment_sequential` | Payment method, installments and amount. |
| `order_reviews` | 99,224 | `review_id, order_id` | Review score 1-5 and dates. |
| `geolocation` | 19,010 | `zip_code_prefix` | Average coordinates per zip prefix. |

### Conventions
- Timestamps are text in `YYYY-MM-DD HH:MM:SS` format; use `date()`, `strftime()` and
  `julianday()` (for example, delivery days = `julianday(delivered_customer_ts) - julianday(purchase_ts)`).
- Money is in Brazilian reais (BRL).
- **Revenue** = `SUM(order_items.price)` for orders whose status is not `canceled` or
  `unavailable` (freight excluded). Total: about R$ 13.49M over 98,199 orders.
- Zip code prefixes are 5-character text (leading zeros kept).

## Cleaning steps

| Source issue | Handling |
|---|---|
| Columns misspelled `product_name_lenght`, `product_description_lenght` | Renamed to `name_length`, `description_length`. |
| Long source column names (`order_purchase_timestamp`, ...) | Shortened (`purchase_ts`, ...). |
| 2 categories without an English translation | Translated manually (`pc_gamer`, `portable_kitchen_food_processors`). |
| 610 products without a category | Kept, `category` is NULL. |
| City names with inconsistent case and accents | Trimmed, lowercased, accents removed. |
| Review comments (free text, may contain personal details) | Dropped; only `has_comment` (0/1) is kept. |
| Geolocation: 1M points, 262k exact duplicates, 47 points outside Brazil | Duplicates and outside points dropped, then averaged to one row per zip prefix. |
| Some orders have more than one review (547) and some review ids repeat across orders | Key is `(review_id, order_id)`. |

Every source row of the other tables is kept; there are no orphan foreign keys in the source.

## Known data limitations
- Orders run from Sep 2016 to Oct 2018, but 2016 has only 329 orders and the data **tapers off
  after 2018-08-21** (about 250 orders per day before, 187, 144, 99, ... after; Sep-Oct 2018 have
  only 20 orders). For trends, weekly reports and forecasts use 2017-01-01 to 2018-08-19
  (the last full Monday-Sunday week is 2018-08-13 to 2018-08-19).
- 775 orders (mostly `unavailable` / `canceled`) have no items.
- 8 orders are `delivered` without a delivery timestamp.
- 279 customers have a zip prefix that is not in `geolocation`.
- `customer_id` changes with every order; count people with `COUNT(DISTINCT customer_unique_id)`.
