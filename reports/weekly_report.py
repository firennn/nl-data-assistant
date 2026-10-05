"""Weekly report builder (owner: owner2).

Uses only shared/ (run_query, MetricResult, ChartSpec, get_llm) and the revenue forecast in
modeling/. The Olist data ends in 2018, so every function takes an explicit `week_end` date
instead of using today's date. Without a mapping the report reads the Olist tables; with a
reports.sales.SalesMapping it reads the mapped table of any database (e.g. an upload).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from modeling.forecast import forecast_weekly_revenue
from modeling.train import load_weekly_revenue
from reports import sales
from reports.anomalies import (
    AnomalyConfig,
    find_daily_anomalies,
    flag_metric_changes,
    rolling_zscore,
)
from reports.charts import ReportChart, build_charts
from reports.metrics import METRICS, MetricDefinition
from reports.summary import build_facts, write_summary
from shared.llm import LLMProvider
from shared.models import MetricResult

logger = logging.getLogger(__name__)

FORECAST_WEEKS = 4

__all__ = [
    "ReportChart",
    "WeeklyReport",
    "build_report",
    "compute_weekly_metrics",
    "detect_anomalies",
    "week_bounds",
]


@dataclass
class WeeklyReport:
    week_start: date
    week_end: date
    metrics: list[MetricResult]
    anomalies: pd.DataFrame  # columns: date, metric, value, expected, z_score, method
    charts: list[ReportChart] = field(default_factory=list)
    forecast: pd.DataFrame | None = None  # see modeling.forecast.forecast_weekly_revenue
    summary: str = ""  # executive summary in plain language
    metric_changes: pd.DataFrame | None = None  # week-over-week changes above the threshold
    summary_source: str = ""  # "llm", or "fallback" if the plain summary was used
    facts: str = ""  # the computed facts the summary is based on
    currency: str = "BRL"  # unit of money values ("" if unknown)
    dataset: str = ""  # name of the data the report describes, shown under the title


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
    return _compare_weeks(list(METRICS.values()), week_end, db_path)


def _compare_weeks(
    definitions: list[MetricDefinition], week_end: date, db_path: str | Path | None
) -> list[MetricResult]:
    week_start, week_end = week_bounds(week_end)
    prev_start, prev_end = week_bounds(week_start - timedelta(days=1))
    results = []
    for metric in definitions:
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

    Each point is compared with the mean and standard deviation of the `window` points before
    it (no look-ahead). Returns the flagged rows with columns date, value, expected, z_score.
    The method and its limits are described in reports/anomalies.py.
    """
    return rolling_zscore(series, window=window, z_threshold=z_threshold)


def build_report(
    week_end: date,
    *,
    db_path: str | Path | None = None,
    llm: LLMProvider | None = None,
    config: AnomalyConfig | None = None,
    mapping: sales.SalesMapping | None = None,
    dataset: str = "",
) -> WeeklyReport:
    """Assemble the full report for the week ending on `week_end`.

    Metrics, anomalies, charts and the forecast are computed first; the executive summary is
    then written from those numbers only (see reports/summary.py). `llm` defaults to
    shared.llm.get_llm(); if it fails, the report still has a plain summary. With `mapping`
    the report is built from the mapped table instead of the Olist tables.
    """
    config = config or AnomalyConfig()
    week_start, week_end = week_bounds(week_end)
    if mapping is None:
        currency = "BRL"
        metrics = compute_weekly_metrics(week_end, db_path=db_path)
        anomalies = find_daily_anomalies(week_end, config=config, db_path=db_path)
        charts = build_charts(week_end, db_path=db_path)
        forecast = _forecast(
            week_end, lambda through: load_weekly_revenue(through=through, db_path=db_path)
        )
    else:
        sales.check_mapping(mapping, db_path)
        currency = mapping.unit
        metrics = _compare_weeks(sales.metric_definitions(mapping), week_end, db_path)
        anomalies = find_daily_anomalies(
            week_end, config=config, db_path=db_path, series=sales.daily_series(mapping)
        )
        charts = sales.build_charts(mapping, week_end, db_path)
        forecast = _forecast(
            week_end,
            lambda through: sales.weekly_revenue(mapping, through=through, db_path=db_path),
        )
    changes = flag_metric_changes(metrics, threshold_pct=config.change_threshold_pct)
    facts = build_facts(
        metrics=metrics,
        metric_changes=changes,
        anomalies=anomalies,
        top_category=_top_row(charts, "category", ("revenue_brl", "revenue")),
        top_state=_top_row(charts, "state", ("orders",)),
        forecast=forecast,
        currency=currency,
    )
    summary = write_summary(facts, llm)
    return WeeklyReport(
        week_start=week_start,
        week_end=week_end,
        metrics=metrics,
        anomalies=anomalies,
        charts=charts,
        forecast=forecast,
        summary=summary.text,
        metric_changes=changes,
        summary_source=summary.source,
        facts=facts,
        currency=currency,
        dataset=dataset,
    )


def _forecast(week_end: date, load_history) -> pd.DataFrame | None:
    """Forecast from the weekly history up to the last Sunday on or before `week_end`.
    `load_history(through)` returns the weekly revenue (columns week, revenue)."""
    through = week_end - timedelta(days=(week_end.weekday() + 1) % 7)
    try:
        history = load_history(through)
        return forecast_weekly_revenue(history, horizon_weeks=FORECAST_WEEKS)
    except ValueError as exc:
        logger.warning("No forecast for the week ending %s: %s", week_end, exc)
        return None


def _top_row(charts: list[ReportChart], label: str, values: tuple[str, ...]) -> tuple | None:
    """(label, value) of the first row of the first chart with the label and one of the value
    columns, if any."""
    for chart in charts:
        value = next((v for v in values if v in chart.data.columns), None)
        if value and label in chart.data.columns and not chart.data.empty:
            first = chart.data.iloc[0]
            return first[label], first[value].item()
    return None
