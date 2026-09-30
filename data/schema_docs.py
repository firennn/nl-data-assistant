"""Table and column descriptions stored in the `_schema_docs` table.

These descriptions are included in the schema text used for SQL generation, so they should
explain meaning, units and pitfalls in one short line each. An empty column name ("") holds the
description of the table itself.
"""

from __future__ import annotations

SCHEMA_DOCS: dict[str, dict[str, str]] = {
    "customers": {
        "": "One row per order-customer pair. Use customer_unique_id to count distinct people.",
        "customer_id": "Key used by orders; a new id is created for every order.",
        "customer_unique_id": "Stable id of the person across orders (use for repeat customers).",
        "zip_code_prefix": "First 5 digits of the customer's zip code (text, keeps leading zeros).",
        "city": "Customer city, lowercase without accents.",
        "state": "Two-letter Brazilian state code, e.g. SP, RJ, MG.",
    },
    "sellers": {
        "": "One row per marketplace seller.",
        "zip_code_prefix": "First 5 digits of the seller's zip code.",
        "city": "Seller city, lowercase.",
        "state": "Two-letter Brazilian state code.",
    },
    "products": {
        "": "One row per product listed on the marketplace.",
        "category": "English category name, e.g. bed_bath_table. NULL when unknown.",
        "category_pt": "Original Portuguese category name.",
        "name_length": "Number of characters in the product name.",
        "description_length": "Number of characters in the product description.",
        "photos_qty": "Number of product photos.",
        "weight_g": "Product weight in grams.",
        "length_cm": "Package length in cm.",
        "height_cm": "Package height in cm.",
        "width_cm": "Package width in cm.",
    },
    "orders": {
        "": (
            "One row per order (Sep 2016 - Oct 2018; data is complete only from 2017-01-01 "
            "to about 2018-08-21). Revenue is usually computed from order_items."
        ),
        "status": (
            "Order status: delivered, shipped, canceled, unavailable, invoiced, processing, "
            "created, approved."
        ),
        "purchase_ts": "When the customer placed the order ('YYYY-MM-DD HH:MM:SS').",
        "approved_ts": "When the payment was approved.",
        "delivered_carrier_ts": "When the order was handed to the carrier.",
        "delivered_customer_ts": "When the customer received the order (NULL if not delivered).",
        "estimated_delivery_date": "Delivery date promised to the customer at purchase.",
    },
    "order_items": {
        "": "One row per item in an order. An order with 3 units has 3 rows.",
        "order_item_id": "Position of the item in the order (1, 2, 3, ...).",
        "shipping_limit_ts": "Deadline for the seller to hand the item to the carrier.",
        "price": (
            "Item price in BRL, excluding freight. Revenue = SUM(price) for orders whose "
            "status is not canceled or unavailable."
        ),
        "freight_value": "Freight cost for the item in BRL.",
    },
    "order_payments": {
        "": "One row per payment of an order; an order can be paid with several methods.",
        "payment_sequential": "Order of the payment within the order (1, 2, ...).",
        "payment_type": "credit_card, boleto, voucher, debit_card or not_defined.",
        "installments": "Number of installments chosen by the customer.",
        "payment_value": "Amount paid in BRL (includes freight).",
    },
    "order_reviews": {
        "": "Customer satisfaction reviews. A few orders have more than one review.",
        "score": "Review score from 1 (worst) to 5 (best).",
        "has_comment": "1 if the customer wrote a comment (the text itself is not stored).",
        "created_date": "Date the satisfaction survey was sent.",
        "answered_ts": "When the customer answered the survey.",
    },
    "geolocation": {
        "": (
            "Average coordinates per zip code prefix. Join on zip_code_prefix from customers "
            "or sellers; a few prefixes are missing."
        ),
        "latitude": "Average latitude of the prefix.",
        "longitude": "Average longitude of the prefix.",
        "city": "Most common city name for the prefix.",
        "state": "Two-letter Brazilian state code.",
    },
}
