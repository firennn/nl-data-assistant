# Weekly report and revenue forecast

This page describes the automated weekly report (`reports/`) and the revenue forecast
(`modeling/`), with sample output from the full dataset. Design decisions are listed in
[DECISIONS.md](DECISIONS.md); interfaces are in [ARCHITECTURE.md](ARCHITECTURE.md).

## Quick start

```bash
python -m reports.export --week-end 2018-08-19   # writes reports/output/weekly_report_2018-08-19.html and .json
python -m modeling.train                         # retrains the forecast, writes modeling/artifacts/*.json
```

From Python (this is what the dashboard calls):

```python
from reports.export import generate_report

result = generate_report("2018-08-19")
result.html_path, result.json_path, result.summary   # summary is a JSON-ready dict
```

The data is complete from 2017-01-01 to about 2018-08-21, so 2018-08-19 is the last full week
and the default. More details, including scheduling examples, are in
[reports/README.md](../reports/README.md).

## How the report is built

```
compute_weekly_metrics ─┐
find_daily_anomalies ───┤
flag_metric_changes ────┼─> facts (computed numbers only) ─> LLM summary ─> number check ─┐
build_charts ───────────┤                                                               ├─> HTML + JSON
forecast_weekly_revenue ┘                                                               │
                                                   plain summary if the check fails ────┘
```

| Part | File | Method |
|---|---|---|
| Metrics | `reports/metrics.py` | Six SQL metrics for the 7 days ending on `week_end`, compared with the 7 days before. Revenue = sum of item prices of orders not canceled or unavailable (freight excluded). Reviews and deliveries count in the week they happen; new customers by their first counted order. |
| Week-over-week changes | `reports/anomalies.py` | A metric is flagged when it moved by 20% or more against the previous week. |
| Daily anomalies | `reports/anomalies.py` | Daily revenue and orders are compared with the mean and standard deviation of the 28 days before each day (the day itself is never part of its baseline). A day is flagged at \|z\| >= 3. |
| Charts | `reports/charts.py` | 12-week revenue trend, top 10 categories by revenue, top 10 states by orders. Each chart is a `ChartSpec` plus data, drawn with `shared.charts.render_chart`. |
| Forecast | `modeling/forecast.py` | See below. |
| Summary | `reports/summary.py` | The LLM gets a short list of computed facts, never raw rows. Every number in its reply must match a fact (rounding allowed); otherwise, or if the call fails, a plain summary of the facts is used. |

Thresholds are in `AnomalyConfig`. A new metric, daily series, detector or chart is one
decorated function in its file.

**Limits.** The z-score ignores weekday patterns, the day after a very large spike has a raised
baseline, and days whose baseline has no variation are never flagged. Week-over-week changes
only compare two weeks, so a slow decline over several weeks can stay below the threshold.

## Sample output: week of 2018-08-13 to 2018-08-19

| Metric | This week | Previous week | Change |
|---|---|---|---|
| Revenue | 244,675.59 BRL | 275,772.04 BRL | -11.3% |
| Orders | 1,852 | 1,961 | -5.6% |
| Average order value | 132.11 BRL | 140.63 BRL | -6.1% |
| New customers | 1,791 | 1,907 | -6.1% |
| Average review score | 4.17 | 4.17 | -0.1% |
| On-time delivery rate | 90.6% | 93.9% | -3.4% |

- No metric changed by 20% or more, and no day was flagged as an anomaly.
- Top category by revenue: health_beauty (31,551.34 BRL). State with the most orders: SP (908).
- The 12-week trend shows a dip in early July (about 155,000 BRL per week) and a recovery to
  about 275,000 BRL in early August before this week's decline.
- New customers are almost equal to orders because about 97% of customers in this dataset order
  only once.

Running the anomaly check over every week of the data gives 10 flags on 8 days. The largest is
Black Friday 2017 (2017-11-24): 1,166 orders against an expected 166 (z = 24.1) and
152,653.74 BRL revenue against an expected 24,153.80 BRL (z = 18.7).

When no LLM API key is set, the report uses the plain summary, for example: "Revenue:
244,675.59 BRL (previous week 275,772.04 BRL, change -11.3%). Orders: 1,852 ..." and the HTML
report notes that the plain summary was used.

## Revenue forecast

**Data.** 85 full weeks of revenue (Monday to Sunday) from 2017-01-02 to 2018-08-19. This is
too short to model yearly seasonality.

**Models.**

- *Naive baseline*: every future week equals the last observed week.
- *Damped-trend exponential smoothing (Holt)*: follows the recent level and a trend that fades
  out over time. The three smoothing parameters are chosen by grid search on one-step-ahead
  errors of the training data only.

**Evaluation.** Rolling-origin backtest: for each of the last 20 weeks, each model is trained
on the weeks before it and forecasts the next 4 weeks (80 forecasts per model). The split is
always by time, never random.

| Model | MAE | MAPE | RMSE at 1 / 2 / 3 / 4 weeks ahead |
|---|---|---|---|
| Naive baseline | 40,601 BRL | 20.5% | 38,600 / 54,763 / 60,206 / 60,354 |
| Holt (damped) | 39,085 BRL | 19.9% | 40,902 / 51,687 / 54,431 / 54,982 |

The model is kept only if its MAE is lower than the baseline's, so Holt is used. The gain is
small: about 4% lower MAE overall, the baseline is better one week ahead, and the result
depends on the test window (Holt is better over the last 10, 20 and 30 weeks but has a higher
MAPE over the last 40, which include Black Friday). Weekly revenue here behaves close to a
random walk, so errors of about 20% are expected from any simple model.

**Interval.** The 80% range is the forecast plus or minus 1.28 times the backtest RMSE of the
chosen model at each horizon, so it reflects how wrong the model has been on this data.

| Week starting | Forecast | 80% range |
|---|---|---|
| 2018-08-20 | 254,632 BRL | 202,212 to 307,052 |
| 2018-08-27 | 255,457 BRL | 189,215 to 321,699 |
| 2018-09-03 | 256,118 BRL | 186,359 to 325,877 |
| 2018-09-10 | 256,646 BRL | 186,181 to 327,112 |

`python -m modeling.train` writes the chosen model, its parameters and this forecast to
`modeling/artifacts/forecast_model.json`, and the backtest scores to
`modeling/artifacts/forecast_metrics.json` (both gitignored).

## Testing

All parts run on a small fixture database (`tests/conftest.py`) with a scripted LLM, so the
tests need no API key or dataset. They check each metric against hand-calculated values, the
anomaly detector on hand-made series (spikes, drops, no look-ahead), the forecast backtest
(training only on the past, falling back to the baseline), the number check in the summary,
and every section of the HTML and JSON output.

## Not included yet

- PDF export: needs a new library in `requirements.txt`; the HTML report can be printed to PDF
  from a browser.
- A scheduled GitHub Actions workflow: an example is in `reports/README.md`.
