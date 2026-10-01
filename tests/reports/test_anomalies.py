"""Anomaly detection: the rolling z-score on hand-made series, the daily series on the fixture
database, and the week-over-week change flags."""

import math
from datetime import date

import pandas as pd
import pytest

from reports import anomalies
from reports.anomalies import (
    ANOMALY_COLUMNS,
    CHANGE_COLUMNS,
    DETECTION_COLUMNS,
    AnomalyConfig,
    daily_orders,
    daily_revenue,
    find_daily_anomalies,
    flag_metric_changes,
    rolling_zscore,
)
from reports.weekly_report import detect_anomalies
from shared.models import MetricResult


def daily(values, start="2018-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="D"))


# Baseline alternates 10 and 12: mean 11, sample std about 1.07 over 8 points.
BASE = [10, 12] * 4


def test_spike_is_flagged_with_expected_value_from_the_window_before():
    result = rolling_zscore(daily(BASE + [30]), window=8, z_threshold=3.0)
    assert list(result.columns) == DETECTION_COLUMNS
    assert len(result) == 1
    row = result.iloc[0]
    assert row["date"] == date(2018, 1, 9)
    assert row["value"] == 30
    assert row["expected"] == pytest.approx(11.0)
    assert row["z_score"] > 3


def test_drops_are_flagged_too():
    result = rolling_zscore(daily(BASE + [0]), window=8)
    assert len(result) == 1
    assert result.iloc[0]["z_score"] < -3


def test_no_look_ahead_and_no_flags_without_full_history():
    # The spike sits at position 3, before 8 days of history exist, so it is not flagged.
    assert rolling_zscore(daily([10, 12, 10, 100] + BASE), window=8).empty
    # A later spike must not change the baseline of earlier days.
    early = rolling_zscore(daily(BASE + [11, 30]), window=8)
    assert list(early["date"]) == [date(2018, 1, 10)]


def test_threshold_is_configurable():
    series = daily(BASE + [14])  # about 2.8 standard deviations above the mean
    assert rolling_zscore(series, window=8, z_threshold=3.0).empty
    assert len(rolling_zscore(series, window=8, z_threshold=2.0)) == 1


def test_flat_baseline_is_never_flagged():
    # Zero spread would give an infinite z-score; the day is skipped instead.
    assert rolling_zscore(daily([5] * 8 + [50]), window=8).empty


def test_invalid_settings_are_rejected():
    with pytest.raises(ValueError):
        rolling_zscore(daily(BASE), window=1)
    with pytest.raises(ValueError):
        rolling_zscore(daily(BASE), z_threshold=0)


def test_contract_function_uses_the_rolling_zscore():
    series = daily(BASE + [30])
    pd.testing.assert_frame_equal(detect_anomalies(series), rolling_zscore(series))


def test_daily_series_fill_missing_days_and_skip_canceled_orders(sample_db):
    start, end = date(2018, 1, 1), date(2018, 1, 10)
    revenue = daily_revenue(start, end, sample_db)
    assert len(revenue) == 10
    assert revenue[pd.Timestamp("2018-01-02")] == pytest.approx(155.9)
    assert revenue[pd.Timestamp("2018-01-05")] == pytest.approx(249.9)
    assert revenue[pd.Timestamp("2018-01-09")] == 0  # only the canceled order o3
    assert revenue.sum() == pytest.approx(405.8)
    assert daily_orders(start, end, sample_db).sum() == 2


def test_find_daily_anomalies_keeps_only_days_in_the_report_week(monkeypatch):
    series = daily(BASE * 4 + [30] + [11] * 6 + [30], start="2018-01-01")
    monkeypatch.setattr(anomalies, "DAILY_SERIES", {"Revenue": lambda s, e, db: series})
    config = AnomalyConfig(daily_window=8)
    # Spikes on 2018-02-02 and 2018-02-09; the week 02-02 .. 02-08 contains only the first.
    result = find_daily_anomalies(date(2018, 2, 8), config=config)
    assert list(result.columns) == ANOMALY_COLUMNS
    assert list(result["date"]) == [date(2018, 2, 2)]
    assert result.iloc[0]["metric"] == "Revenue"
    assert result.iloc[0]["method"] == "rolling z-score"


def test_find_daily_anomalies_on_the_fixture_database(sample_db):
    # 2018-01-02 has a zero-spread baseline (no sales before it), so it is skipped; by
    # 2018-01-05 the baseline has spread and the second sale stands out for both series.
    result = find_daily_anomalies(date(2018, 1, 7), db_path=sample_db)
    assert list(result.columns) == ANOMALY_COLUMNS
    assert list(result["metric"]) == ["Orders", "Revenue"]
    assert set(result["date"]) == {date(2018, 1, 5)}
    assert result.set_index("metric").loc["Revenue", "value"] == pytest.approx(249.9)


def test_find_daily_anomalies_returns_empty_frame_when_nothing_is_flagged(sample_db):
    result = find_daily_anomalies(date(2017, 6, 30), db_path=sample_db)
    assert result.empty
    assert list(result.columns) == ANOMALY_COLUMNS


def test_new_detector_is_picked_up_from_the_registry(monkeypatch):
    series = daily([1.0] * 10)
    monkeypatch.setattr(anomalies, "DAILY_SERIES", {"Orders": lambda s, e, db: series})
    monkeypatch.setattr(anomalies, "DETECTORS", {})

    @anomalies.register_detector("always last day")
    def last_day(s, config):
        return pd.DataFrame(
            {"date": [s.index[-1].date()], "value": [1.0], "expected": [1.0], "z_score": [0.0]}
        )

    result = find_daily_anomalies(date(2018, 1, 10), config=AnomalyConfig(daily_window=3))
    assert list(result["method"]) == ["always last day"]


def metric(name, value, previous, unit="BRL"):
    return MetricResult(
        name, value, date(2018, 1, 1), date(2018, 1, 7), previous, "previous week", unit
    )


def test_week_over_week_changes_above_threshold_are_flagged_largest_first():
    metrics = [
        metric("Revenue", 80.0, 100.0),  # -20%: on the threshold, flagged
        metric("Orders", 90.0, 100.0, "orders"),  # -10%: not flagged
        metric("New customers", 150.0, 100.0, "customers"),  # +50%: flagged
        metric("Average review score", math.nan, None, "score"),  # no comparison
    ]
    result = flag_metric_changes(metrics, threshold_pct=20.0)
    assert list(result.columns) == CHANGE_COLUMNS
    assert list(result["metric"]) == ["New customers", "Revenue"]
    assert result.iloc[1]["change_pct"] == pytest.approx(-20.0)
    assert flag_metric_changes(metrics, threshold_pct=60.0).empty
