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
