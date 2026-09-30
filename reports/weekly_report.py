"""Weekly report builder (owner: owner2).

Uses only shared/ (run_query, MetricResult, ChartSpec, get_llm) and modeling.forecast's
public function. The Olist data ends in 2018, so every function takes an explicit
`week_end` date instead of using today's date.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from shared.models import ChartSpec, MetricResult


@dataclass
class ReportChart:
    title: str
    spec: ChartSpec
    data: pd.DataFrame


@dataclass
class WeeklyReport:
    week_start: date
    week_end: date
    metrics: list[MetricResult]
    anomalies: pd.DataFrame  # columns: date, metric, value, expected, z_score
    charts: list[ReportChart] = field(default_factory=list)
    forecast: pd.DataFrame | None = None  # see modeling.forecast.forecast_weekly_revenue
    summary: str = ""  # executive summary in plain language


def compute_weekly_metrics(week_end: date) -> list[MetricResult]:
    """Return the key metrics for the 7 days ending on `week_end`, compared with the week before.

    TODO(owner2):
    - Metrics: revenue (see docs/DECISIONS.md: sum of order_items.price, excluding canceled
      and unavailable orders), number of orders,
      average order value, new customers (first order by customer_unique_id), average review
      score, on-time delivery rate (delivered_customer_ts <= estimated_delivery_date).
    - Use shared.db.run_query with parameters (?), never string formatting.
    - Fill comparison_value / comparison_label="previous week"; units "BRL", "orders", "%".
    """
    raise NotImplementedError


def detect_anomalies(
    series: pd.Series, *, window: int = 8, z_threshold: float = 3.0
) -> pd.DataFrame:
    """Flag points in a daily or weekly series that deviate strongly from the recent trend.

    TODO(owner2):
    - Start with a rolling mean/std z-score over `window` periods (no look-ahead).
    - Return a DataFrame with columns: date, value, expected, z_score (only flagged rows).
    - Document the method and its limits in the report.
    """
    raise NotImplementedError


def build_report(week_end: date) -> WeeklyReport:
    """Assemble the full report for the week ending on `week_end`.

    TODO(owner2):
    - Call compute_weekly_metrics, detect_anomalies on daily revenue and orders,
      and modeling.forecast.forecast_weekly_revenue for the next 4 weeks.
    - Add charts as ReportChart(ChartSpec, DataFrame): 12-week revenue trend (line),
      top categories (bar), orders by state (bar).
    - Write the executive summary with shared.llm.get_llm(), passing only computed numbers
      (never raw rows) and asking for 4-6 plain sentences.
    """
    raise NotImplementedError
