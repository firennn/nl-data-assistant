"""Metric registry for the weekly report (owner: owner2).

Each metric is one function decorated with @register_metric. It receives the first and last
day of a period (both inclusive) and the database path, and returns a single number, or NaN
when the period has nothing to measure (for example, no reviews). To add a metric, add one
function to this file; compute_weekly_metrics picks it up automatically.

Definitions follow docs/DECISIONS.md:
- Revenue and orders count orders placed in the period, excluding canceled and unavailable
  orders. Revenue is the sum of order_items.price (freight excluded).
- Reviews and deliveries are counted in the period in which they happened.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from shared.db import run_query

DbPath = str | Path | None
MetricFn = Callable[[date, date, DbPath], float]


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    unit: str
    compute: MetricFn


METRICS: dict[str, MetricDefinition] = {}


def register_metric(name: str, unit: str) -> Callable[[MetricFn], MetricFn]:
    """Add the decorated function to METRICS under `name`. Order of registration is kept."""

    def decorator(fn: MetricFn) -> MetricFn:
        if name in METRICS:
            raise ValueError(f"Metric {name!r} is already registered.")
        METRICS[name] = MetricDefinition(name=name, unit=unit, compute=fn)
        return fn

    return decorator


def _scalar(sql: str, start: date, end: date, db_path: DbPath) -> float:
    """Run a query that takes (start, end) as parameters and returns one value."""
    df = run_query(sql, [start.isoformat(), end.isoformat()], db_path=db_path)
    value = df.iat[0, 0] if not df.empty else None
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return math.nan
    return float(value)


@register_metric("Revenue", "BRL")
def revenue(start: date, end: date, db_path: DbPath = None) -> float:
    sql = """
        SELECT COALESCE(SUM(oi.price), 0)
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.order_id
        WHERE o.status NOT IN ('canceled', 'unavailable')
          AND date(o.purchase_ts) BETWEEN ? AND ?
    """
    return _scalar(sql, start, end, db_path)


@register_metric("Orders", "orders")
def orders(start: date, end: date, db_path: DbPath = None) -> float:
    sql = """
        SELECT COUNT(*)
        FROM orders o
        WHERE o.status NOT IN ('canceled', 'unavailable')
          AND date(o.purchase_ts) BETWEEN ? AND ?
    """
    return _scalar(sql, start, end, db_path)


@register_metric("Average order value", "BRL")
def average_order_value(start: date, end: date, db_path: DbPath = None) -> float:
    # Revenue divided by counted orders; NULL (no orders) becomes NaN.
    sql = """
        SELECT SUM(oi.price) / NULLIF(COUNT(DISTINCT o.order_id), 0)
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.order_id
        WHERE o.status NOT IN ('canceled', 'unavailable')
          AND date(o.purchase_ts) BETWEEN ? AND ?
    """
    return _scalar(sql, start, end, db_path)


@register_metric("New customers", "customers")
def new_customers(start: date, end: date, db_path: DbPath = None) -> float:
    # A customer (customer_unique_id) is new in the period of their first counted order.
    sql = """
        WITH first_orders AS (
            SELECT c.customer_unique_id, MIN(date(o.purchase_ts)) AS first_date
            FROM orders o
            JOIN customers c ON c.customer_id = o.customer_id
            WHERE o.status NOT IN ('canceled', 'unavailable')
            GROUP BY c.customer_unique_id
        )
        SELECT COUNT(*) FROM first_orders WHERE first_date BETWEEN ? AND ?
    """
    return _scalar(sql, start, end, db_path)


@register_metric("Average review score", "score")
def average_review_score(start: date, end: date, db_path: DbPath = None) -> float:
    sql = """
        SELECT AVG(score)
        FROM order_reviews
        WHERE date(created_date) BETWEEN ? AND ?
    """
    return _scalar(sql, start, end, db_path)


@register_metric("On-time delivery rate", "%")
def on_time_delivery_rate(start: date, end: date, db_path: DbPath = None) -> float:
    # Orders delivered in the period; on time if delivered on or before the estimated date.
    sql = """
        SELECT 100.0 * AVG(
            CASE WHEN date(delivered_customer_ts) <= date(estimated_delivery_date)
                 THEN 1 ELSE 0 END)
        FROM orders
        WHERE delivered_customer_ts IS NOT NULL
          AND estimated_delivery_date IS NOT NULL
          AND date(delivered_customer_ts) BETWEEN ? AND ?
    """
    return _scalar(sql, start, end, db_path)
