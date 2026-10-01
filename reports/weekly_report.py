"""Weekly report builder (owner: owner2).

Uses only shared/ (run_query, MetricResult, ChartSpec, get_llm) and modeling.forecast's
public function. The Olist data ends in 2018, so every function takes an explicit
`week_end` date instead of using today's date.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from reports.metrics import METRICS
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


def week_bounds(week_end: date) -> tuple[date, date]:
    """Return (week_start, week_end) for the 7 days ending on `week_end`, both inclusive."""
    return week_end - timedelta(days=6), week_end


def compute_weekly_metrics(
    week_end: date, *, db_path: str | Path | None = None
) -> list[MetricResult]:
    """Return the key metrics for the 7 days ending on `week_end`, compared with the week before.

    Metrics come from the registry in reports/metrics.py, in registration order. A value is
    NaN when the week has nothing to measure; the comparison is then None.
    """
    week_start, week_end = week_bounds(week_end)
    prev_start, prev_end = week_bounds(week_start - timedelta(days=1))
    results = []
    for metric in METRICS.values():
        value = metric.compute(week_start, week_end, db_path)
        previous = metric.compute(prev_start, prev_end, db_path)
        results.append(
            MetricResult(
                name=metric.name,
                value=value,
                period_start=week_start,
                period_end=week_end,
                comparison_value=None if math.isnan(previous) else previous,
                comparison_label="previous week",
                unit=metric.unit,
            )
        )
    return results


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
