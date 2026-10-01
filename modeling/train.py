"""Retrain and evaluate the weekly revenue forecast (owner: owner2).

    python -m modeling.train                      # history up to 2018-08-19, 4-week horizon
    python -m modeling.train --through 2018-06-24 --horizon 4 --out modeling/artifacts

Loads weekly revenue from the database, backtests every model in modeling.forecast.MODELS,
fits the chosen model on the full history and writes two JSON files to the output folder
(gitignored): forecast_model.json (model, parameters, forecast) and forecast_metrics.json
(backtest MAE/MAPE for every model).
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from modeling.forecast import forecast_weekly_revenue
from shared.db import run_query

ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"
FIRST_FULL_WEEK = date(2017, 1, 2)  # Monday; earlier weeks have almost no orders
LAST_FULL_WEEK_END = date(2018, 8, 19)  # the data is incomplete after 2018-08-21


def load_weekly_revenue(
    *, start: date = FIRST_FULL_WEEK, through: date = LAST_FULL_WEEK_END, db_path=None
) -> pd.DataFrame:
    """Weekly revenue (same definition as the report) for Monday-Sunday weeks from the week
    starting `start` to the week ending `through` (a Sunday). Weeks without sales are 0."""
    if through.weekday() != 6:
        raise ValueError(f"through must be a Sunday, got {through} ({through:%A}).")
    sql = """
        SELECT date(o.purchase_ts, 'weekday 0', '-6 days') AS week, SUM(oi.price) AS revenue
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.order_id
        WHERE o.status NOT IN ('canceled', 'unavailable')
          AND date(o.purchase_ts) BETWEEN ? AND ?
        GROUP BY week
        ORDER BY week
    """
    first_week = start - timedelta(days=start.weekday())
    df = run_query(sql, [first_week.isoformat(), through.isoformat()], db_path=db_path)
    weeks = pd.date_range(first_week, through - timedelta(days=6), freq="7D").date
    revenue = dict(zip(pd.to_datetime(df["week"]).dt.date, df["revenue"], strict=True))
    return pd.DataFrame({"week": weeks, "revenue": [float(revenue.get(w, 0.0)) for w in weeks]})


def train(
    *,
    through: date = LAST_FULL_WEEK_END,
    horizon: int = 4,
    out_dir: Path = ARTIFACTS_DIR,
    db_path=None,
) -> dict[str, object]:
    """Backtest, fit and save the forecast; return what was written."""
    history = load_weekly_revenue(through=through, db_path=db_path)
    forecast = forecast_weekly_revenue(history, horizon_weeks=horizon)
    trained = {
        "trained_through": through.isoformat(),
        "n_weeks": len(history),
        "horizon_weeks": horizon,
        "model": forecast.attrs["model"],
        "beats_baseline": forecast.attrs["beats_baseline"],
        "params": forecast.attrs["params"],
        "forecast": [
            {"week": row.week.isoformat(), **{k: round(getattr(row, k), 2) for k in _VALUES}}
            for row in forecast.itertuples()
        ],
    }
    metrics = {"trained_through": through.isoformat(), "backtest": forecast.attrs["scores"]}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "forecast_model.json").write_text(json.dumps(trained, indent=2), encoding="utf-8")
    (out_dir / "forecast_metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return {"model": trained, "metrics": metrics}


_VALUES = ("forecast", "lower", "upper")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Retrain the weekly revenue forecast.")
    parser.add_argument(
        "--through",
        type=date.fromisoformat,
        default=LAST_FULL_WEEK_END,
        help="last week end (a Sunday) used for training, YYYY-MM-DD",
    )
    parser.add_argument("--horizon", type=int, default=4, help="weeks to forecast")
    parser.add_argument("--out", type=Path, default=ARTIFACTS_DIR, help="output folder")
    parser.add_argument("--db", type=Path, default=None, help="database file (default DB_PATH)")
    args = parser.parse_args(argv)

    result = train(through=args.through, horizon=args.horizon, out_dir=args.out, db_path=args.db)
    model, scores = result["model"], result["metrics"]["backtest"]
    print(f"Trained on {model['n_weeks']} weeks through {model['trained_through']}.")
    print("Backtest (rolling origin):")
    for name, s in scores.items():
        mape = "n/a" if s["mape"] is None else f"{s['mape']:.1f}%"
        print(f"  {name:12} MAE {s['mae']:>12,.0f} BRL   MAPE {mape:>6}")
    verdict = "beats" if model["beats_baseline"] else "does NOT beat"
    print(f"Chosen model: {model['model']} ({verdict} the naive baseline)")
    for row in model["forecast"]:
        print(
            f"  week of {row['week']}: {row['forecast']:>12,.0f}  "
            f"(80% range {row['lower']:,.0f} - {row['upper']:,.0f})"
        )
    print(f"Saved to {args.out}")


if __name__ == "__main__":
    main()
