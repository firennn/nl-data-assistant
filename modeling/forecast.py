"""Weekly revenue forecasting (owner: owner2)."""

from __future__ import annotations

import pandas as pd

FORECAST_COLUMNS = ["week", "forecast", "lower", "upper"]


def forecast_weekly_revenue(history: pd.DataFrame, horizon_weeks: int = 4) -> pd.DataFrame:
    """Forecast weekly revenue for the next `horizon_weeks` weeks.

    Args:
        history: DataFrame with columns `week` (week start date) and `revenue`, one row per week,
            sorted ascending, with no gaps.
        horizon_weeks: number of future weeks to forecast.

    Returns:
        DataFrame with columns FORECAST_COLUMNS; `lower`/`upper` form an 80% interval.

    TODO(owner2):
    - Start with a seasonal-naive or moving-average baseline, then try one stronger model
      (e.g. exponential smoothing) and keep it only if it beats the baseline.
    - Add a backtest (rolling origin, MAPE and MAE) and report both models' scores.
    - Keep dependencies small; record any new library in docs/DECISIONS.md.
    """
    raise NotImplementedError
