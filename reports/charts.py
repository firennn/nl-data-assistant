"""Charts for the weekly report (owner: owner2).

Each chart is one function decorated with @register_chart. It receives the last day of the
report week and the database path and returns a ReportChart: a library-independent ChartSpec
plus the data to draw. Rendering happens later with shared.charts.render_chart, so the HTML
report and the dashboard draw the same charts. To add a chart, add one function to this file.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from reports.metrics import revenue
from shared.db import run_query
from shared.models import ChartSpec

DbPath = str | Path | None

TREND_WEEKS = 12
TOP_N = 10


@dataclass
class ReportChart:
    title: str
    spec: ChartSpec
    data: pd.DataFrame


ChartFn = Callable[[date, DbPath], ReportChart]
CHARTS: dict[str, ChartFn] = {}


def register_chart(name: str) -> Callable[[ChartFn], ChartFn]:
    """Add the decorated function to CHARTS under `name`. Order of registration is kept."""

    def decorator(fn: ChartFn) -> ChartFn:
        if name in CHARTS:
            raise ValueError(f"Chart {name!r} is already registered.")
        CHARTS[name] = fn
        return fn

    return decorator


def build_charts(week_end: date, *, db_path: DbPath = None) -> list[ReportChart]:
    """Return every registered chart for the week ending on `week_end`."""
    return [make_chart(week_end, db_path) for make_chart in CHARTS.values()]


@register_chart("revenue_trend")
def revenue_trend(week_end: date, db_path: DbPath = None) -> ReportChart:
    """Weekly revenue for the last TREND_WEEKS weeks, oldest first, using the report's
    revenue definition so the last point matches the Revenue metric."""
    week_ends = [week_end - timedelta(weeks=n) for n in range(TREND_WEEKS - 1, -1, -1)]
    data = pd.DataFrame(
        {
            "week_ending": week_ends,
            "revenue_brl": [revenue(end - timedelta(days=6), end, db_path) for end in week_ends],
        }
    )
    title = f"Weekly revenue, last {TREND_WEEKS} weeks"
    spec = ChartSpec(kind="line", title=title, x="week_ending", y="revenue_brl")
    return ReportChart(title=title, spec=spec, data=data)


@register_chart("top_categories")
def top_categories(week_end: date, db_path: DbPath = None) -> ReportChart:
    """Product categories with the highest revenue in the report week."""
    sql = """
        SELECT COALESCE(p.category, 'unknown') AS category, SUM(oi.price) AS revenue_brl
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.order_id
        JOIN products p ON p.product_id = oi.product_id
        WHERE o.status NOT IN ('canceled', 'unavailable')
          AND date(o.purchase_ts) BETWEEN ? AND ?
        GROUP BY category
        ORDER BY revenue_brl DESC, category
        LIMIT ?
    """
    data = run_query(sql, [*_week_params(week_end), TOP_N], db_path=db_path)
    title = f"Top {TOP_N} categories by revenue"
    spec = ChartSpec(kind="bar", title=title, x="category", y="revenue_brl")
    return ReportChart(title=title, spec=spec, data=data)


@register_chart("orders_by_state")
def orders_by_state(week_end: date, db_path: DbPath = None) -> ReportChart:
    """Customer states with the most orders in the report week."""
    sql = """
        SELECT c.state AS state, COUNT(*) AS orders
        FROM orders o
        JOIN customers c ON c.customer_id = o.customer_id
        WHERE o.status NOT IN ('canceled', 'unavailable')
          AND date(o.purchase_ts) BETWEEN ? AND ?
        GROUP BY c.state
        ORDER BY orders DESC, state
        LIMIT ?
    """
    data = run_query(sql, [*_week_params(week_end), TOP_N], db_path=db_path)
    title = f"Top {TOP_N} states by orders"
    spec = ChartSpec(kind="bar", title=title, x="state", y="orders")
    return ReportChart(title=title, spec=spec, data=data)


def _week_params(week_end: date) -> list[str]:
    return [(week_end - timedelta(days=6)).isoformat(), week_end.isoformat()]
