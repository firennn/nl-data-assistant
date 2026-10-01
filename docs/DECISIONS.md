# Decisions

Short log of design decisions. Newest last.

| Date | Decision | Reason |
|---|---|---|
| 2026-10-01 | Use SQLite as the database | Zero setup, one file, enough for about 100k orders; easy for three people to rebuild locally. |
| 2026-10-01 | Use the Olist Brazilian E-Commerce dataset | Real, anonymized data with 9 related tables, so questions need real joins; about two years of orders gives enough weekly history for trends and forecasting. |
| 2026-10-01 | Do not commit raw data or the database; rebuild with a script | Keeps the repository small, respects the dataset license (CC BY-NC-SA 4.0) and makes the build reproducible. |
| 2026-10-01 | Three independent read-only safety layers (validator, read-only connection, SQLite authorizer) | SQL comes from a model, so a single regex check is not enough; each layer blocks writes on its own. |
| 2026-10-01 | Modules communicate only through `shared/`, the database and listed public entry points | Lets three people work in parallel with few merge conflicts; interfaces are documented in ARCHITECTURE.md. |
| 2026-10-01 | Provider-independent LLM wrapper with a free-tier primary provider and an optional fallback provider | Keeps cost at zero for a student project; the fallback covers rate limits; providers can be added or removed in one file. |
| 2026-10-01 | Scripted `FakeProvider` and a fixture database for tests | Tests are fast, deterministic and run in CI without API keys or the full dataset. |
| 2026-10-01 | `ChartSpec` is library-independent; Plotly renders it in one place (`shared/charts.py`) | The agent decides what to show; the dashboard and HTML reports draw it the same way. |
| 2026-10-01 | Revenue = sum of `order_items.price` for orders not canceled or unavailable (freight excluded) | One shared definition so the agent, reports and evaluation give the same numbers. |
| 2026-10-01 | Download the dataset with `kagglehub` (anonymous) with a manual CSV fallback | No Kaggle account or token needed for anyone on the team; still works offline with CSVs in `data/raw/`. |
| 2026-10-01 | Drop review comment text, keep only a `has_comment` flag | Free text can contain names or other personal details; scores are enough for the analysis. |
| 2026-10-01 | Aggregate geolocation to one row per zip prefix (mean coordinates, most common city) | The source has about 1M noisy points with many duplicates; one row per prefix makes a clean join key. |
| 2026-10-01 | Keep all orders and statuses instead of filtering | Filtering is a question-level choice (e.g. revenue excludes canceled orders); the database stays a faithful copy. |
| 2026-10-01 | Store timestamps as ISO text | SQLite has no datetime type; ISO text sorts correctly and works with `date()`, `strftime()` and `julianday()`. |
| 2026-10-01 | Build into a temporary file and replace the database only after all checks pass | A failed or interrupted build never leaves a broken database behind. |
| 2026-10-01 | Leave out sample values for id columns in the schema text | Hash ids carry no meaning for SQL generation and cost tokens on every request. |
| 2026-10-02 | Agent replies in JSON with an explicit action (`sql` or `clarify`) | The agent can tell answers from follow-up questions without guessing from free text, and malformed replies can be detected and retried. |
| 2026-10-02 | Ask a clarifying question only when readings differ a lot; otherwise state the assumption | Too many questions make the assistant tiring to use; stated assumptions keep answers transparent. |
| 2026-10-02 | Retry failed SQL with the database error message, at most 2 extra attempts | Most generation errors (wrong column, syntax) are fixed once the model sees the error; a cap bounds cost and latency. |
| 2026-10-02 | Never retry unsafe SQL | A request to change data must not be rephrased until it slips through; the user gets a clear refusal instead. |
| 2026-10-02 | Rule-based chart selection instead of asking the LLM | Deterministic, testable and free; the rules cover the common result shapes. |
| 2026-10-02 | Business rules (revenue, distinct customers, data date range) in the system prompt | Keeps answers consistent with the definitions used by the reports and evaluation. |
| 2026-10-02 | Separate LLM call for the explanation, with a plain summary as fallback | The explanation is based on the actual result rows, and a failed explanation never loses a correct answer. |
| 2026-10-02 | Retry temporary LLM errors inside the provider (server busy, per-minute limits), but fail fast on a used-up daily quota | Busy spells are short and a retry usually succeeds; waiting on a daily quota only wastes time, so the fallback or another model is used instead. |
| 2026-10-02 | Default model `gemini-3.1-flash-lite` instead of the larger Flash model | In testing, the larger model's free tier allowed only 20 requests per day (about 10 questions) and was often overloaded; Flash-Lite answered the test questions correctly with a larger free quota. |
| 2026-10-02 | Reserve extra output tokens for the model's internal reasoning | Newer models count reasoning tokens toward the output limit; without headroom short replies could come back empty. |
| 2026-10-01 | Weekly report metrics live in a registry (`reports/metrics.py`), one decorated function per metric | Adding a metric is a one-file change, and the report, tests and exports pick it up automatically. |
| 2026-10-01 | A report week is the 7 days ending on `week_end` (inclusive), compared with the 7 days before | The dataset ends in 2018, so the week is always explicit instead of based on today's date. |
| 2026-10-01 | Reviews and on-time delivery are counted in the week the review was written or the order was delivered | The weekly numbers then describe what happened that week, instead of changing later as older orders get delivered or reviewed. |
| 2026-10-01 | New customers are counted by their first order that is not canceled or unavailable | Matches the revenue definition, so a canceled first order does not count as gaining a customer. |
| 2026-10-01 | A metric with nothing to measure in a week (no orders, reviews or deliveries) is NaN and shown as "n/a" | Avoids reporting a misleading 0 for an average or a rate. |
| 2026-10-01 | Daily anomalies use a rolling z-score against the previous 28 days (threshold 3), with no look-ahead | Simple to explain and test; 28 days smooths out weekday effects; on the full data it flags Black Friday 2017 (z = 24) and only a handful of other days. |
| 2026-10-01 | Days whose baseline has zero spread are not flagged | Avoids division by zero and infinite scores at the start of the data; this limit is documented in `reports/anomalies.py`. |
| 2026-10-01 | Week-over-week changes of 20% or more are flagged separately from daily anomalies | A slow drift across a week does not show up as a daily spike, but managers still want to see large weekly moves. |
| 2026-10-01 | Anomaly thresholds live in one `AnomalyConfig`, and daily series and detectors in registries in `reports/anomalies.py` | Thresholds can be tuned in one place, and adding a series or detector is a one-function change. |
| 2026-10-01 | Report charts are a registry in `reports/charts.py` that returns a `ChartSpec` plus data, rendered only by `shared.charts.render_chart` | Adding a chart is a one-function change, and the HTML report and dashboard draw charts the same way. |
| 2026-10-01 | The revenue trend reuses the Revenue metric for each week | The last point of the chart always matches the revenue number in the KPI table. |
| 2026-10-01 | Category and state charts show the top 10 for the report week, ties sorted by name | Keeps bar charts readable (there are 27 states and over 70 categories) and the order deterministic. |
| 2026-10-01 | `ReportChart` moved to `reports/charts.py` and is imported into `reports/weekly_report.py` | Avoids a circular import between the chart and report modules; `weekly_report.ReportChart` still works. |
| 2026-10-01 | Forecast weekly revenue with damped-trend exponential smoothing (Holt), compared against a naive baseline (last week's value) | About 85 weeks of data is too short for yearly seasonality; a damped trend follows the level without extrapolating growth too far. Both models are plain Python, so no new library is needed. |
| 2026-10-01 | Evaluate with a rolling-origin backtest over the last 20 weeks (horizon 4), never a random split | Each forecast uses only the weeks before it, like a real weekly run. MAE and MAPE are reported for both models. |
| 2026-10-01 | Keep the stronger model only if its backtest MAE is lower; the baseline wins ties and is used when history is too short | The report always says whether the model beat the baseline. On the full data Holt is about 4% better (MAE 39,085 vs 40,601 BRL), but not in every backtest window, so the margin is small. |
| 2026-10-01 | 80% forecast interval = forecast +/- 1.28 x backtest RMSE at each horizon | The range reflects how wrong the chosen model has actually been on this data, and widens with the horizon. |
| 2026-10-01 | `python -m modeling.train` retrains and writes the model and metrics as JSON to `modeling/artifacts/` (gitignored) | Retraining is one command, and results are reproducible without committing generated files. |
