import math
from datetime import date, timedelta

import pandas as pd
import pytest

from modeling.forecast import (
    FORECAST_COLUMNS,
    MODELS,
    Forecaster,
    HoltDampedForecaster,
    NaiveForecaster,
    backtest,
    choose_model,
    forecast_weekly_revenue,
    score,
)


def history(values, start=date(2018, 1, 1)):
    return pd.DataFrame(
        {"week": [start + timedelta(weeks=i) for i in range(len(values))], "revenue": values}
    )


TREND = [100.0 + 10 * t for t in range(40)]


def test_public_interface_exists():
    assert callable(forecast_weekly_revenue)
    assert FORECAST_COLUMNS == ["week", "forecast", "lower", "upper"]


def test_forecast_shape():
    """Returns horizon_weeks rows with FORECAST_COLUMNS and lower <= forecast <= upper."""
    noisy = [200 + 5 * t + (15 if t % 3 == 0 else -10) for t in range(40)]
    result = forecast_weekly_revenue(history(noisy), horizon_weeks=6)
    assert list(result.columns) == FORECAST_COLUMNS
    assert len(result) == 6
    assert (result["lower"] <= result["forecast"]).all()
    assert (result["forecast"] <= result["upper"]).all()
    assert (result["lower"] >= 0).all()


def test_forecast_weeks_continue_after_the_last_week():
    result = forecast_weekly_revenue(history(TREND), horizon_weeks=3)
    last = date(2018, 1, 1) + timedelta(weeks=39)
    assert list(result["week"]) == [last + timedelta(weeks=h) for h in (1, 2, 3)]


def test_holt_beats_naive_on_a_trend_and_is_chosen():
    result = forecast_weekly_revenue(history(TREND), horizon_weeks=4)
    assert result.attrs["model"] == "holt_damped"
    assert result.attrs["beats_baseline"] is True
    scores = result.attrs["scores"]
    assert scores["holt_damped"]["mae"] < scores["naive"]["mae"]
    # The trend continues upward (damped, so a bit less than +10 per week).
    assert 480 < result["forecast"].iloc[0] <= 500
    assert result["forecast"].is_monotonic_increasing


def test_baseline_is_kept_when_the_model_does_not_beat_it():
    result = forecast_weekly_revenue(history([500.0] * 30), horizon_weeks=4)
    # A flat series is a tie, and the simpler model wins ties.
    assert result.attrs["model"] == "naive"
    assert result.attrs["beats_baseline"] is False
    assert list(result["forecast"]) == [500.0] * 4
    assert list(result["lower"]) == list(result["upper"]) == [500.0] * 4


def test_short_history_falls_back_to_baseline_with_widening_interval():
    result = forecast_weekly_revenue(history([100.0, 120.0, 110.0, 130.0]), horizon_weeks=4)
    assert result.attrs["model"] == "naive"
    assert result.attrs["scores"] == {}
    widths = list(result["upper"] - result["lower"])
    assert widths == sorted(widths)
    assert widths[0] > 0
    assert list(result["forecast"]) == [130.0] * 4


@pytest.mark.parametrize(
    "bad, message",
    [
        (pd.DataFrame({"week": [date(2018, 1, 1)]}), "missing columns"),
        (pd.DataFrame(columns=["week", "revenue"]), "empty"),
        (history([1.0, 2.0]).iloc[::-1], "no gaps"),
        (history([1.0, 2.0, 3.0]).drop(index=1), "no gaps"),
        (history([1.0, math.nan, 3.0]), "missing revenue"),
    ],
)
def test_invalid_history_is_rejected(bad, message):
    with pytest.raises(ValueError, match=message):
        forecast_weekly_revenue(bad)


def test_horizon_must_be_positive():
    with pytest.raises(ValueError, match="horizon_weeks"):
        forecast_weekly_revenue(history(TREND), horizon_weeks=0)


def test_naive_repeats_the_last_value():
    assert NaiveForecaster().fit([3.0, 5.0, 7.0]).predict(3) == [7.0, 7.0, 7.0]


def test_holt_needs_three_values_and_reports_its_parameters():
    with pytest.raises(ValueError):
        HoltDampedForecaster().fit([1.0, 2.0])
    model = HoltDampedForecaster().fit(TREND)
    assert set(model.params()) == {"alpha", "beta", "phi"}


class SpyForecaster(Forecaster):
    """Records how much history each fit sees."""

    seen: list[int] = []

    def fit(self, values):
        SpyForecaster.seen.append(len(values))
        self._last = values[-1]
        return self

    def predict(self, horizon):
        return [self._last] * horizon


def test_backtest_trains_only_on_the_past():
    SpyForecaster.seen = []
    values = [float(v) for v in range(30)]
    errors = backtest(values, SpyForecaster, horizon=4, n_origins=5, min_train=12)
    # Origins 22..26: each fit sees exactly the weeks before its origin.
    assert SpyForecaster.seen == [22, 23, 24, 25, 26]
    assert len(errors) == 5 * 4
    first = errors[errors["origin"] == 22]
    assert list(first["actual"]) == [22.0, 23.0, 24.0, 25.0]  # the weeks after the origin
    assert list(first["forecast"]) == [21.0] * 4  # the last training value


def test_backtest_needs_enough_history():
    with pytest.raises(ValueError, match="at least 16 values"):
        backtest([1.0] * 10, NaiveForecaster, horizon=4, min_train=12)


def test_score_computes_mae_mape_and_rmse_per_horizon():
    errors = pd.DataFrame(
        {
            "origin": [1, 1, 2, 2],
            "h": [1, 2, 1, 2],
            "actual": [100.0, 0.0, 200.0, 50.0],
            "forecast": [90.0, 10.0, 220.0, 50.0],
            "error": [10.0, -10.0, -20.0, 0.0],
        }
    )
    s = score(errors)
    assert s["mae"] == pytest.approx(10.0)
    assert s["mape"] == pytest.approx((10 + 10 + 0) / 3)  # the zero actual is skipped
    assert s["rmse_by_horizon"] == pytest.approx([math.sqrt(250), math.sqrt(50)])
    assert s["n_forecasts"] == 4
    assert score(errors.assign(actual=0.0))["mape"] is None


def test_choose_model_prefers_lower_mae_and_baseline_on_ties():
    assert choose_model({"naive": {"mae": 5.0}, "holt_damped": {"mae": 4.0}}) == "holt_damped"
    assert choose_model({"holt_damped": {"mae": 5.0}, "naive": {"mae": 5.0}}) == "naive"


def test_registered_models():
    assert set(MODELS) == {"naive", "holt_damped"}
