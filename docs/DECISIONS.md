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
