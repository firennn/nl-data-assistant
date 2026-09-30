-- Target schema for the Olist e-commerce database (SQLite).
-- Used by data/build_db.py and by the test fixture database.
-- Timestamps are stored as ISO-8601 text: 'YYYY-MM-DD HH:MM:SS' (use date()/strftime()).
-- Money columns are in Brazilian reais (BRL).

PRAGMA foreign_keys = ON;

CREATE TABLE customers (
    customer_id        TEXT PRIMARY KEY,   -- one id per order-customer pair
    customer_unique_id TEXT NOT NULL,      -- stable id for the same person across orders
    zip_code_prefix    TEXT,
    city               TEXT,
    state              TEXT                -- two-letter Brazilian state code
);

CREATE TABLE sellers (
    seller_id       TEXT PRIMARY KEY,
    zip_code_prefix TEXT,
    city            TEXT,
    state           TEXT
);

CREATE TABLE products (
    product_id          TEXT PRIMARY KEY,
    category            TEXT,              -- English category name
    category_pt         TEXT,              -- original Portuguese category name
    name_length         INTEGER,
    description_length  INTEGER,
    photos_qty          INTEGER,
    weight_g            REAL,
    length_cm           REAL,
    height_cm           REAL,
    width_cm            REAL
);

CREATE TABLE orders (
    order_id                TEXT PRIMARY KEY,
    customer_id             TEXT NOT NULL REFERENCES customers (customer_id),
    status                  TEXT NOT NULL,
    purchase_ts             TEXT NOT NULL,
    approved_ts             TEXT,
    delivered_carrier_ts    TEXT,
    delivered_customer_ts   TEXT,
    estimated_delivery_date TEXT
);

CREATE TABLE order_items (
    order_id          TEXT NOT NULL REFERENCES orders (order_id),
    order_item_id     INTEGER NOT NULL,    -- 1..n position of the item within the order
    product_id        TEXT NOT NULL REFERENCES products (product_id),
    seller_id         TEXT NOT NULL REFERENCES sellers (seller_id),
    shipping_limit_ts TEXT,
    price             REAL NOT NULL,
    freight_value     REAL NOT NULL,
    PRIMARY KEY (order_id, order_item_id)
);

CREATE TABLE order_payments (
    order_id           TEXT NOT NULL REFERENCES orders (order_id),
    payment_sequential INTEGER NOT NULL,
    payment_type       TEXT NOT NULL,
    installments       INTEGER,
    payment_value      REAL NOT NULL,
    PRIMARY KEY (order_id, payment_sequential)
);

CREATE TABLE order_reviews (
    review_id   TEXT NOT NULL,
    order_id    TEXT NOT NULL REFERENCES orders (order_id),
    score       INTEGER NOT NULL CHECK (score BETWEEN 1 AND 5),
    has_comment INTEGER NOT NULL DEFAULT 0, -- 1 if the customer left a text comment (text not stored)
    created_date TEXT,
    answered_ts  TEXT,
    PRIMARY KEY (review_id, order_id)
);

-- One row per zip code prefix (the source has many points per prefix; they are averaged).
-- Join on zip_code_prefix from customers or sellers; not every prefix is present.
CREATE TABLE geolocation (
    zip_code_prefix TEXT PRIMARY KEY,
    latitude        REAL,
    longitude       REAL,
    city            TEXT,
    state           TEXT
);

-- Table and column descriptions used for prompts and documentation.
-- column_name = '' holds the description of the table itself.
CREATE TABLE _schema_docs (
    table_name  TEXT NOT NULL,
    column_name TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL,
    PRIMARY KEY (table_name, column_name)
);

CREATE INDEX idx_orders_customer ON orders (customer_id);
CREATE INDEX idx_orders_purchase_ts ON orders (purchase_ts);
CREATE INDEX idx_orders_status ON orders (status);
CREATE INDEX idx_order_items_product ON order_items (product_id);
CREATE INDEX idx_order_items_seller ON order_items (seller_id);
CREATE INDEX idx_order_reviews_order ON order_reviews (order_id);
CREATE INDEX idx_customers_unique ON customers (customer_unique_id);
CREATE INDEX idx_products_category ON products (category);
