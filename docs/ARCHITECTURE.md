# Architecture

This document is the contract between the modules. If you change an interface described here,
update this file in the same pull request and tell the other owners.

## Principle

Modules communicate only through `shared/` and the SQLite database. They may call each other's
**public entry points** listed below, but never internal helpers.

```
                    +---------------------------------------------+
                    |                  shared/                    |
                    | config | db (read-only) | schema | models    |
                    | llm (providers + fallback) | charts          |
                    +---------------------------------------------+
                        ^            ^             ^           ^
   data/ --builds--> [ SQLite DB ]   |             |           |
                        ^            |             |           |
                        |         agent/        reports/   evaluation/
                        |            ^          modeling/      ^
                        |            |             ^           |
                        +------------+--- dashboard/ ----------+
```

Question flow (agent):

```
question -> describe_schema + schema_to_prompt -> LLM -> {"action": "sql" | "clarify"}
         -> validate_read_only -> run_query -> (on SQL error: retry with the error, capped)
         -> choose chart (rules) -> LLM explanation -> QueryResult
```

## agent/ (owner1)

| Module | Purpose |
|---|---|
| `sql_agent.py` | `SQLAgent.ask(question, history=None) -> QueryResult` — the only entry point other modules use |
| `prompts.py` | Generic system prompts and examples, plus builders that add a `DatasetProfile` (`build_sql_system`, `build_explain_system`, `build_sql_prompt`, `build_explain_prompt`) |
| `chart_selector.py` | `choose_chart(df, title="") -> ChartSpec`, deterministic rules |
| `cli.py` | `python -m agent.cli "question"` or interactive mode; `--chart out.html` saves the chart |

Behavior of `ask`:
- The LLM replies with JSON: `{"action": "sql", "sql": ..., "assumptions": ...}`,
  `{"action": "clarify", "question": ...}` or `{"action": "refuse", "reason": ...}` (requests to
  change data or unrelated questions). A clarifying question is only accepted on the first
  attempt and is returned as `needs_clarification=True`; a refusal is returned as `error`.
- Pass the conversation as `history=[("user", ...), ("assistant", ...)]` so a reply to a
  clarifying question or a follow-up ("now by month") is understood.
- SQL errors, timeouts and malformed replies are retried with the error message, up to
  `AGENT_MAX_RETRIES` extra attempts (default 2). `attempts` in the result counts the tries.
- Unsafe SQL is never run and never retried; the result has an error message.
- LLM failures (rate limit, no key) are returned as `error`, never raised.
- A missing database, a file that is not a SQLite database, or a schema that takes too long to
  read is returned as `error` without calling the LLM. The schema text in the prompt is kept
  within `SCHEMA_MAX_CHARS` (12,000 characters).
- Chart rules: empty/no numbers -> table; 1x1 number -> metric; period column + number -> line;
  one category + one number (<= 20 rows) -> bar; two numbers -> scatter; otherwise table.
- The explanation is 2-4 sentences from a second LLM call; if that call fails, a short summary
  is used so the answer is still returned.
- If the question uses a relative period ("last month"), the SQL also returns the period it
  resolves to and the explanation names it (e.g. "May 2023").
- The prompts are generic; dataset-specific context comes from the `profile` argument
  (a `DatasetProfile`, default `shared.profiles.OLIST_PROFILE`). Pass a profile for any other
  database, e.g. an uploaded one. Without a date range in the profile, relative periods
  ("last month") are taken relative to the latest date in the data. Sample values are read and
  sent to the LLM only if `profile.include_samples` is true.

## shared/ (owner1)

| Module | Interface |
|---|---|
| `config.py` | `get_settings(env_file=None) -> Settings` with `db_path`, `llm_provider`, `llm_model`, `llm_api_key`, `llm_fallback_provider`, `llm_fallback_model`, `llm_fallback_api_key`, `agent_max_retries` |
| `db.py` | `run_query(sql, params=None, *, db_path=None, max_rows=10_000, timeout_s=10) -> DataFrame`; `validate_read_only(sql) -> str`; errors `UnsafeQueryError`, `QueryError`, `QueryTimeoutError`; `connect_read_only(db_path=None)` for trusted internal SQL only |
| `schema.py` | `describe_schema(db_path=None, *, sample_values=3, tables=None, timeout_s=10.0) -> SchemaInfo` (raises `QueryTimeoutError` past the time limit, `QueryError` for a file that is not a readable SQLite database); `schema_to_prompt(schema, *, include_samples=True, max_chars=None) -> str`; limits as constants: `DESCRIBE_TIMEOUT_S`, `SCHEMA_MAX_CHARS`, `SCHEMA_COLUMN_CAPS`, `SCHEMA_OMITTED_TABLE_NAMES` |
| `models.py` | `ChartSpec`, `QueryResult`, `MetricResult`, `DatasetProfile` (see below) |
| `profiles.py` | `OLIST_PROFILE`: business rules, currency, date range and examples for the demo database |
| `llm.py` | `get_llm(settings=None) -> LLMProvider`; `LLMProvider.complete(prompt, *, system=None, temperature=0.0, max_tokens=2048, json_mode=False) -> LLMResponse`; `register_provider(name, factory)`; errors `LLMError`, `LLMConfigError`, `LLMTransientError`, `LLMRateLimitError`, `LLMQuotaExceededError`; `FakeProvider` for tests |
| `charts.py` | `render_chart(df, spec) -> plotly.graph_objects.Figure` |

### Read-only guarantee
`run_query` applies three independent layers:
1. `validate_read_only`: one statement only, must start with `SELECT` or `WITH`, and no write or
   admin keywords (checked after removing string literals and comments).
2. The connection is opened with `mode=ro` and `PRAGMA query_only = ON`.
3. An SQLite authorizer allows only read, select, function and recursive-CTE actions.

It also caps rows (`df.attrs["truncated"]`) and stops queries that exceed the time limit.

Every connection (`connect_read_only`, used by `run_query` and schema inspection) also sets
`PRAGMA trusted_schema = OFF`, so views, triggers and defaults stored in a database file (for
example an uploaded one) cannot call functions with side effects.

### Schema text size limit
When `max_chars` is set and the full schema text is longer, `schema_to_prompt` shortens it in
this order until it fits: leave out sample values, then descriptions, then keep only key
columns plus the first 30, 20 or 10 columns per table, then leave out tables at the end (the
first 20 left-out names are listed), and as a last resort cut the text. A line always says what
was left out. A schema that fits is returned unchanged.

### Data models

```python
ChartSpec(kind: "bar"|"line"|"scatter"|"pie"|"table"|"metric", title="", x=None, y=None, color=None)

QueryResult(question, sql=None, data: DataFrame | None = None, chart: ChartSpec | None = None,
            explanation="", error=None, needs_clarification=False,
            clarifying_question=None, attempts=0)        # .ok property

MetricResult(name, value, period_start: date, period_end: date,
             comparison_value=None, comparison_label=None, unit=None)   # .change_pct property

DatasetProfile(name,
               description="",                  # e.g. "an e-commerce marketplace (Olist, Brazil)"
               rules: list[str] = [],           # business rules, one prompt line each
               currency=None,                   # e.g. "Brazilian reais (BRL)"
               currency_format=None,            # e.g. "R$ 1,234.56", used in explanations
               date_range: tuple[str, str] | None = None,  # ("YYYY-MM-DD", "YYYY-MM-DD")
               examples="",                     # dataset-specific examples; "" = generic ones
               filter_example=None,             # how to word a filter in explanations
               include_samples=False)           # sample values in the schema text
```
`DatasetProfile` defaults are deliberately minimal: a new profile sends no sample values to the
LLM unless it sets `include_samples=True`.

### LLM providers
`get_llm()` returns the provider named by `LLM_PROVIDER`. If `LLM_FALLBACK_PROVIDER` is set and
can be created, the result is wrapped in `FallbackLLM`, which retries once on the fallback
provider when the primary fails with a transient error (rate limit, server or network error).
Providers are plain classes registered in `shared/llm.py`; adding one (for example a locally
hosted fine-tuned model) does not change any caller.

The Gemini provider retries temporary failures itself: "server busy" (5xx) after 2 s and 6 s,
and per-minute rate limits after the wait the API suggests. A used-up **daily** quota raises
`LLMQuotaExceededError` immediately (a subclass of `LLMRateLimitError`, so the fallback provider
still takes over). Free-tier quotas are per model, so switching `LLM_MODEL` also works.

## Database (owner1)
SQLite file at `DB_PATH` (default `data/olist.db`), built by `python -m data.build_db` from
`data/schema.sql`. Tables: `customers`, `sellers`, `products`, `orders`, `order_items`,
`order_payments`, `order_reviews`, `geolocation`, plus the internal `_schema_docs`
(descriptions used in prompts). Full schema: `docs/SCHEMA.md`.

## Uploaded data (owner1)
`data.upload.build_user_db(sources, out_dir, *, dayfirst=None) -> UploadedDatabase` builds a new
SQLite file from uploaded CSV files (one table per file) or from one SQLite file. `sources` are file paths or file-like
objects with a `.name` (e.g. uploaded files); the database gets a new random name in `out_dir`.
Cleaning up old files is the caller's job.

```python
UploadedDatabase(db_path: Path,                       # open read-only (SQLAgent and run_query do)
                 profile: DatasetProfile,             # pass to SQLAgent(profile=...)
                 tables: dict[str, tuple[int, int]],  # table -> (rows, columns)
                 notes: list[str])                    # what was changed or assumed, for the user

up = build_user_db(files, out_dir)
agent = SQLAgent(db_path=up.db_path, profile=up.profile)
```

- Names: table and column names are cleaned to snake_case (accents removed, other characters
  become `_`, no leading `_`, a leading digit gets `t_`/`c_`, SQL keywords get a `_` suffix,
  duplicates get `_2`, `_3`). Every rename is listed in `notes`.
- Reading: UTF-8 (with or without byte-order mark), else Windows-1252 or Latin-1 with a note;
  `,`, `;`, tab or `|` as separator.
- Types are strict: a column is `INTEGER`/`REAL` only if every non-empty value is a number
  (decimal commas are read in `;` files), and a date only if every value is a date. Dates are
  stored as ISO text like the demo database. Numbers with leading zeros (e.g. zip codes) stay
  text. Empty cells and `NA`/`N/A`/`null`/`none`/`nan` become NULL.
- Ambiguous dates such as `03/04/2023` are read as day/month unless `dayfirst=False`; dates with
  a day above 12 are always read the way the data shows. A note says which order was used.
- SQLite uploads (`.sqlite`, `.sqlite3`, `.db`, one file on its own) are copied and checked
  before use: the file must start with the SQLite header and pass `PRAGMA quick_check`, and
  files with views, triggers or virtual tables are rejected. Table and column names are then
  cleaned in the copy with the same rules as CSV headers (foreign keys follow the renames), and
  the copy is stored as a single file (no WAL). The uploaded file itself is never changed.
- The profile describes the tables and, for CSV uploads, lists each date column's range as a rule. It sets no
  `date_range` (completeness of user data is unknown) and `include_samples=False`.
- Limits (constants in `data/upload.py`): `MAX_UPLOAD_BYTES` = 50 MB for all files together
  (checked before any file is read), `MAX_FILES` = 10, `MAX_ROWS` = 1,000,000 and
  `MAX_COLUMNS` = 200 per CSV file or SQLite table, `MAX_TABLES` = 100 per SQLite file.
- Problems raise `UploadError` (a `ValueError`) with a message meant for the user, e.g.
  `The upload is 63.2 MB; the limit is 50 MB.`, `report.xlsx is not a CSV or SQLite file.` or
  `shop.db contains views or triggers (v), which are not supported.` A failed build leaves no
  file behind.

## dashboard/ (owner3)

| Module | Purpose |
|---|---|
| `app.py` | Entry point (`streamlit run dashboard/app.py`): sidebar with the database picker and the page menu |
| `context.py` | `AppContext`: the current `DataSource` (name, `db_path`, `DatasetProfile`, kind `demo` or `upload`), the Olist demo source, and file locations. The only place that decides which database the chat page queries |
| `views/` | One file per page, each defining `VIEW = View(key, title, render, order, icon, olist_only)`; found automatically |
| `usage.py` | Local usage log (`dashboard/usage_log.jsonl`, not committed) |

- Pages call only public entry points: `SQLAgent(db_path=..., profile=...).ask`,
  `reports.export.generate_report`, and the evaluation results files read with
  `evaluation.run_eval.load_results` and summarized by `evaluation.metrics`.
- `olist_only` pages (Reports, Evaluation) always use the Olist demo source. An uploaded
  database becomes another `DataSource` via `dashboard.context.add_source`; the chat page then
  queries it with its own profile.

## Public entry points

| Module | Entry point | Used by |
|---|---|---|
| agent (owner1) | `agent.SQLAgent(llm=None, settings=None, *, db_path=None, max_rows=1000, profile=None).ask(question, history=None) -> QueryResult` | dashboard, evaluation |
| data/upload (owner1) | `data.upload.build_user_db(sources, out_dir, *, dayfirst=None) -> UploadedDatabase`; errors `UploadError` | dashboard |
| reports (owner2) | `reports.export.generate_report(week_end=None, *, out_dir=None, db_path=None, llm=None, pdf=False) -> ReportResult(html_path, json_path, summary, pdf_path)`; `reports.weekly_report.build_report(week_end, *, db_path=None, llm=None, config=None) -> WeeklyReport`; `reports.export.export_html / export_pdf / run_weekly`; CLI `python -m reports.export --week-end YYYY-MM-DD [--pdf]` | dashboard, scheduler |
| modeling (owner2) | `modeling.forecast.forecast_weekly_revenue(history, horizon_weeks=4) -> DataFrame[week, forecast, lower, upper]` (attrs: model, beats_baseline, scores); retrain with `python -m modeling.train` | reports |
| evaluation (owner3) | `evaluation.run_eval.run_evaluation(ask, cases) -> EvalReport`; `load_questions()`; results files in `evaluation/results/` | dashboard |
| dashboard (owner3) | `streamlit run dashboard/app.py` | users |

`run_evaluation` takes the agent as a function (`question -> QueryResult`), so it can evaluate
any agent version or model without importing agent internals.

## Testing
- `tests/conftest.py` provides `sample_db`: a small database built from `data/schema.sql`.
- LLM calls in tests use `shared.llm.FakeProvider`; CI needs no API keys or dataset download.
- Each module has its own test folder under `tests/`.
