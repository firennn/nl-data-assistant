"""Trend and anomaly detection for the weekly report (owner: owner2).

Two checks, both with configurable thresholds (AnomalyConfig):

1. Daily anomalies: every daily series in DAILY_SERIES (revenue, orders) is passed to every
   detector in DETECTORS. The default detector is a rolling z-score: each day is compared with
   the mean and standard deviation of the `daily_window` days before it (the day itself is
   never part of its own baseline, so there is no look-ahead). A day is flagged when
   |z| >= z_threshold.
2. Week-over-week changes: a metric is flagged when it moved by at least
   `change_threshold_pct` percent compared with the previous week.

Adding a daily series or a detector is a one-function change in this file.

Limits: the z-score assumes the recent past is a fair baseline, so the first days after a
sudden level change are flagged and later ones are not; days whose baseline has zero spread
are never flagged; and the method ignores weekday patterns.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from shared.db import run_query
from shared.models import MetricResult

DbPath = str | Path | None
SeriesFn = Callable[[date, date, DbPath], pd.Series]
DetectorFn = Callable[[pd.Series, "AnomalyConfig"], pd.DataFrame]

DETECTION_COLUMNS = ["date", "value", "expected", "z_score"]
ANOMALY_COLUMNS = ["date", "metric", "value", "expected", "z_score", "method"]
CHANGE_COLUMNS = ["metric", "value", "previous", "change_pct", "unit"]


@dataclass(frozen=True)
class AnomalyConfig:
    daily_window: int = 28  # days of history used as the baseline for each day
    z_threshold: float = 3.0  # flag days at least this many standard deviations away
    change_threshold_pct: float = 20.0  # flag week-over-week changes of at least this size


DAILY_SERIES: dict[str, SeriesFn] = {}
DETECTORS: dict[str, DetectorFn] = {}


def register_series(name: str) -> Callable[[SeriesFn], SeriesFn]:
    """Add a daily series loader (start, end, db_path) -> Series indexed by day."""

    def decorator(fn: SeriesFn) -> SeriesFn:
        if name in DAILY_SERIES:
            raise ValueError(f"Series {name!r} is already registered.")
        DAILY_SERIES[name] = fn
        return fn

    return decorator


def register_detector(name: str) -> Callable[[DetectorFn], DetectorFn]:
    """Add a detector (series, config) -> DataFrame with DETECTION_COLUMNS (flagged rows only)."""

    def decorator(fn: DetectorFn) -> DetectorFn:
        if name in DETECTORS:
            raise ValueError(f"Detector {name!r} is already registered.")
        DETECTORS[name] = fn
        return fn

    return decorator


def rolling_zscore(series: pd.Series, *, window: int = 8, z_threshold: float = 3.0) -> pd.DataFrame:
    """Flag points that are at least `z_threshold` standard deviations from the mean of the
    `window` points before them. Points without a full window of history are not flagged.

    Returns the flagged rows with DETECTION_COLUMNS, oldest first.
    """
    if window < 2:
        raise ValueError("window must be at least 2.")
    if z_threshold <= 0:
        raise ValueError("z_threshold must be positive.")
    values = series.astype(float)
    history = values.shift(1).rolling(window, min_periods=window)
    expected = history.mean()
    spread = history.std().replace(0, math.nan)
    z_score = (values - expected) / spread
    flagged = z_score.abs() >= z_threshold
    return pd.DataFrame(
        {
            "date": [_as_date(d) for d in series.index[flagged]],
            "value": values[flagged].to_numpy(),
            "expected": expected[flagged].to_numpy(),
            "z_score": z_score[flagged].to_numpy(),
        },
        columns=DETECTION_COLUMNS,
    )


@register_detector("rolling z-score")
def _rolling_zscore_detector(series: pd.Series, config: AnomalyConfig) -> pd.DataFrame:
    return rolling_zscore(series, window=config.daily_window, z_threshold=config.z_threshold)


def _daily(sql: str, start: date, end: date, db_path: DbPath) -> pd.Series:
    """Run a (day, value) query and return one value per day, with 0 for days without rows."""
    df = run_query(sql, [start.isoformat(), end.isoformat()], db_path=db_path)
    series = pd.Series(df.iloc[:, 1].to_numpy(dtype=float), index=pd.to_datetime(df.iloc[:, 0]))
    days = pd.date_range(start, end, freq="D")
    return series.reindex(days, fill_value=0.0)


@register_series("Revenue")
def daily_revenue(start: date, end: date, db_path: DbPath = None) -> pd.Series:
    sql = """
        SELECT date(o.purchase_ts) AS day, SUM(oi.price) AS revenue
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.order_id
        WHERE o.status NOT IN ('canceled', 'unavailable')
          AND date(o.purchase_ts) BETWEEN ? AND ?
        GROUP BY day
        ORDER BY day
    """
    return _daily(sql, start, end, db_path)


@register_series("Orders")
def daily_orders(start: date, end: date, db_path: DbPath = None) -> pd.Series:
    sql = """
        SELECT date(o.purchase_ts) AS day, COUNT(*) AS orders
        FROM orders o
        WHERE o.status NOT IN ('canceled', 'unavailable')
          AND date(o.purchase_ts) BETWEEN ? AND ?
        GROUP BY day
        ORDER BY day
    """
    return _daily(sql, start, end, db_path)


def find_daily_anomalies(
    week_end: date, *, config: AnomalyConfig | None = None, db_path: DbPath = None
) -> pd.DataFrame:
    """Run every detector on every daily series and return the flagged days in the 7 days
    ending on `week_end`, with ANOMALY_COLUMNS, sorted by date and metric."""
    config = config or AnomalyConfig()
    week_start = week_end - timedelta(days=6)
    history_start = week_start - timedelta(days=config.daily_window)
    frames = []
    for metric, load in DAILY_SERIES.items():
        series = load(history_start, week_end, db_path)
        for method, detect in DETECTORS.items():
            found = detect(series, config)
            found = found[found["date"].between(week_start, week_end)]
            if not found.empty:
                frames.append(found.assign(metric=metric, method=method))
    if not frames:
        return pd.DataFrame(columns=ANOMALY_COLUMNS)
    result = pd.concat(frames, ignore_index=True)[ANOMALY_COLUMNS]
    return result.sort_values(["date", "metric"], ignore_index=True)


def flag_metric_changes(
    metrics: list[MetricResult], *, threshold_pct: float | None = None
) -> pd.DataFrame:
    """Return the metrics whose week-over-week change is at least `threshold_pct` percent
    (either direction), largest change first, with CHANGE_COLUMNS."""
    threshold = AnomalyConfig().change_threshold_pct if threshold_pct is None else threshold_pct
    rows = [
        {
            "metric": m.name,
            "value": m.value,
            "previous": m.comparison_value,
            "change_pct": m.change_pct,
            "unit": m.unit,
        }
        for m in metrics
        if m.change_pct is not None
        and not math.isnan(m.change_pct)
        and abs(m.change_pct) >= threshold
    ]
    result = pd.DataFrame(rows, columns=CHANGE_COLUMNS)
    order = result["change_pct"].abs().sort_values(ascending=False).index
    return result.loc[order].reset_index(drop=True)


def _as_date(value: object) -> date:
    return pd.Timestamp(value).date()
