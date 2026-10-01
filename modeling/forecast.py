"""Weekly revenue forecasting (owner: owner2).

Two models are compared with a rolling-origin backtest (time-based, never random):

- naive: next weeks equal the last observed week (the baseline).
- holt_damped: exponential smoothing with a damped trend (Holt's method). Its smoothing
  parameters are chosen by grid search on one-step-ahead errors of the training data only.

forecast_weekly_revenue keeps the stronger model only if it has a lower backtest MAE than the
baseline; otherwise it falls back to the baseline and says so in the result's attrs. The 80%
interval comes from the backtest errors at each horizon (forecast +/- 1.2816 * RMSE), so it
reflects how wrong the chosen model has actually been on this data.

There are only about 85 full weeks of data, so yearly seasonality is not modeled.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from itertools import product

import pandas as pd

FORECAST_COLUMNS = ["week", "forecast", "lower", "upper"]

Z_80 = 1.2816  # two-sided 80% interval of a normal distribution
BASELINE = "naive"
MIN_TRAIN_WEEKS = 12  # shortest training window used in the backtest


class Forecaster:
    """Fit on a list of past values, then predict the next `horizon` values."""

    name = ""

    def fit(self, values: list[float]) -> Forecaster:
        raise NotImplementedError

    def predict(self, horizon: int) -> list[float]:
        raise NotImplementedError

    def params(self) -> dict[str, float]:
        return {}


class NaiveForecaster(Forecaster):
    name = "naive"

    def fit(self, values: list[float]) -> NaiveForecaster:
        self._last = values[-1]
        return self

    def predict(self, horizon: int) -> list[float]:
        return [self._last] * horizon


@dataclass
class HoltDampedForecaster(Forecaster):
    """Holt's linear trend method with a damped trend.

    level_t = alpha * y_t + (1 - alpha) * (level_{t-1} + phi * trend_{t-1})
    trend_t = beta * (level_t - level_{t-1}) + (1 - beta) * phi * trend_{t-1}
    forecast_{t+h} = level_t + (phi + phi^2 + ... + phi^h) * trend_t
    """

    alphas: tuple[float, ...] = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
    betas: tuple[float, ...] = (0.05, 0.1, 0.2, 0.3)
    phis: tuple[float, ...] = (0.8, 0.9, 0.98)
    best: dict[str, float] = field(default_factory=dict)
    name = "holt_damped"

    def fit(self, values: list[float]) -> HoltDampedForecaster:
        if len(values) < 3:
            raise ValueError("Holt's method needs at least 3 values.")
        best_sse = math.inf
        for alpha, beta, phi in product(self.alphas, self.betas, self.phis):
            sse, level, trend = _holt_pass(values, alpha, beta, phi)
            if sse < best_sse:
                best_sse = sse
                self.best = {"alpha": alpha, "beta": beta, "phi": phi}
                self._level, self._trend = level, trend
        return self

    def predict(self, horizon: int) -> list[float]:
        phi = self.best["phi"]
        damping = 0.0
        out = []
        for h in range(1, horizon + 1):
            damping += phi**h
            out.append(self._level + damping * self._trend)
        return out

    def params(self) -> dict[str, float]:
        return dict(self.best)


def _holt_pass(
    values: list[float], alpha: float, beta: float, phi: float
) -> tuple[float, float, float]:
    """Run the smoothing once; return (sum of squared one-step errors, final level, trend)."""
    level, trend = values[0], values[1] - values[0]
    sse = 0.0
    for y in values[1:]:
        predicted = level + phi * trend
        sse += (y - predicted) ** 2
        new_level = alpha * y + (1 - alpha) * predicted
        trend = beta * (new_level - level) + (1 - beta) * phi * trend
        level = new_level
    return sse, level, trend


MODELS: dict[str, Callable[[], Forecaster]] = {
    "naive": NaiveForecaster,
    "holt_damped": HoltDampedForecaster,
}


def backtest(
    values: list[float],
    make_model: Callable[[], Forecaster],
    *,
    horizon: int = 4,
    n_origins: int = 20,
    min_train: int = MIN_TRAIN_WEEKS,
) -> pd.DataFrame:
    """Rolling-origin backtest. For each origin, fit on values[:origin] only and forecast the
    next `horizon` values. Returns one row per (origin, h) with actual, forecast and error.

    The last `n_origins` possible origins are used, each with at least `min_train` values.
    """
    last_origin = len(values) - horizon
    first_origin = max(min_train, last_origin - n_origins + 1)
    if first_origin > last_origin:
        raise ValueError(
            f"Need at least {min_train + horizon} values for a backtest, got {len(values)}."
        )
    rows = []
    for origin in range(first_origin, last_origin + 1):
        predicted = make_model().fit(values[:origin]).predict(horizon)
        for h, forecast in enumerate(predicted, start=1):
            actual = values[origin + h - 1]
            rows.append(
                {
                    "origin": origin,
                    "h": h,
                    "actual": actual,
                    "forecast": forecast,
                    "error": actual - forecast,
                }
            )
    return pd.DataFrame(rows)


def score(errors: pd.DataFrame) -> dict[str, object]:
    """MAE and MAPE over all backtest rows, plus RMSE per horizon (used for the interval).
    MAPE skips weeks with zero actual revenue and is None if every actual is zero."""
    abs_err = errors["error"].abs()
    nonzero = errors["actual"] != 0
    mape = None
    if nonzero.any():
        mape = float((abs_err[nonzero] / errors.loc[nonzero, "actual"].abs()).mean() * 100)
    rmse_by_h = errors.groupby("h")["error"].apply(lambda e: math.sqrt((e**2).mean()))
    return {
        "mae": float(abs_err.mean()),
        "mape": mape,
        "rmse_by_horizon": [float(v) for v in rmse_by_h],
        "n_forecasts": int(len(errors)),
    }


def evaluate_models(
    values: list[float], *, horizon: int = 4, n_origins: int = 20
) -> dict[str, dict[str, object]]:
    """Backtest every model in MODELS on the same origins and return their scores."""
    return {
        name: score(backtest(values, make, horizon=horizon, n_origins=n_origins))
        for name, make in MODELS.items()
    }


def choose_model(scores: dict[str, dict[str, object]]) -> str:
    """Pick the model with the lowest MAE; the baseline wins ties."""
    best = min(scores, key=lambda name: (scores[name]["mae"], name != BASELINE))
    return best


def forecast_weekly_revenue(history: pd.DataFrame, horizon_weeks: int = 4) -> pd.DataFrame:
    """Forecast weekly revenue for the next `horizon_weeks` weeks.

    Args:
        history: DataFrame with columns `week` (week start date) and `revenue`, one row per week,
            sorted ascending, with no gaps.
        horizon_weeks: number of future weeks to forecast.

    Returns:
        DataFrame with columns FORECAST_COLUMNS; `lower`/`upper` form an 80% interval.
        attrs: "model" (name used), "beats_baseline" (bool), "scores" (backtest MAE/MAPE for
        every model) and "params" (fitted parameters of the model used).
    """
    if horizon_weeks < 1:
        raise ValueError("horizon_weeks must be at least 1.")
    weeks, values = _validate_history(history)
    if len(values) >= MIN_TRAIN_WEEKS + horizon_weeks:
        scores = evaluate_models(values, horizon=horizon_weeks)
        chosen = choose_model(scores)
        rmse = scores[chosen]["rmse_by_horizon"]
    else:
        # Too little history to backtest: use the baseline, and widen the interval with the
        # typical week-to-week change (a random walk's error grows with sqrt(h)).
        scores, chosen = {}, BASELINE
        rmse = [_step_rmse(values) * math.sqrt(h) for h in range(1, horizon_weeks + 1)]
    model = MODELS[chosen]().fit(values)
    future = [weeks[-1] + timedelta(weeks=h) for h in range(1, horizon_weeks + 1)]
    predicted = model.predict(horizon_weeks)
    result = pd.DataFrame(
        {
            "week": future,
            "forecast": predicted,
            "lower": [max(0.0, f - Z_80 * r) for f, r in zip(predicted, rmse, strict=True)],
            "upper": [f + Z_80 * r for f, r in zip(predicted, rmse, strict=True)],
        },
        columns=FORECAST_COLUMNS,
    )
    result.attrs.update(
        model=chosen,
        beats_baseline=chosen != BASELINE,
        scores=scores,
        params=model.params(),
    )
    return result


def _step_rmse(values: list[float]) -> float:
    steps = [b - a for a, b in zip(values, values[1:], strict=False)]
    return math.sqrt(sum(s * s for s in steps) / len(steps)) if steps else 0.0


def _validate_history(history: pd.DataFrame) -> tuple[list, list[float]]:
    missing = {"week", "revenue"} - set(history.columns)
    if missing:
        raise ValueError(f"history is missing columns: {sorted(missing)}")
    if history.empty:
        raise ValueError("history is empty.")
    weeks = [pd.Timestamp(w).date() for w in history["week"]]
    gaps = [b - a for a, b in zip(weeks, weeks[1:], strict=False)]
    if any(gap != timedelta(weeks=1) for gap in gaps):
        raise ValueError("history must be sorted by week, one row per week, with no gaps.")
    values = [float(v) for v in history["revenue"]]
    if any(math.isnan(v) for v in values):
        raise ValueError("history contains missing revenue values.")
    return weeks, values
