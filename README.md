# NL Data Assistant

Ask business questions about an e-commerce dataset in plain language and get an answer with the
SQL used, a result table, a chart and a short explanation. A weekly report job summarizes key
metrics, trends, anomalies and a forecast, and an evaluation set measures how accurate the SQL is.

Dataset: [Olist Brazilian E-Commerce](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)
(about 100k orders, 2016-2018, CC BY-NC-SA 4.0). The raw data is not included in this repository;
a script downloads it and builds a local SQLite database.

## Modules

| Folder | What it does | Owner |
|---|---|---|
| `shared/` | Config, read-only DB access, schema description, data models, LLM wrapper, charts | owner1 |
| `data/` | Dataset download, cleaning and SQLite build | owner1 |
| `agent/` | Natural-language to SQL agent and CLI | owner1 |
| `reports/` | Weekly report: metrics, anomalies, summary, HTML/PDF export | owner2 |
| `modeling/` | Revenue forecast, optional text-to-SQL fine-tuning experiment | owner2 |
| `evaluation/` | Question set with known answers, accuracy and failure analysis | owner3 |
| `dashboard/` | Streamlit app for chat, reports and evaluation results | owner3 |

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for interfaces and data flow and
[docs/DECISIONS.md](docs/DECISIONS.md) for the design decisions.

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements-dev.txt
cp .env.example .env            # then add your API key(s)

python -m data.build_db         # download the dataset and build data/olist.db
python -m agent.cli "Which 5 categories had the highest revenue in 2017?"
streamlit run dashboard/app.py
```

## Asking questions

```bash
python -m agent.cli "What was the total revenue in 2017?"
python -m agent.cli "Orders per month in 2018" --chart orders.html   # also save the chart
python -m agent.cli                                                # interactive mode
```

For each question the agent prints a short explanation, the SQL it ran, the first rows of the
result and the chosen chart type. If a question is ambiguous it asks a follow-up question first;
if a query fails it retries with the database error (up to `AGENT_MAX_RETRIES` times).

From Python:

```python
from agent import SQLAgent

result = SQLAgent().ask("Which 5 categories had the highest revenue?")
result.sql, result.data, result.chart, result.explanation
```

## Tests and lint

```bash
python -m pytest
ruff check . && ruff format --check .
```

The tests use a small fixture database and a scripted LLM, so they need no API key or dataset.

## Safety

All SQL runs through `shared.db.run_query`, which accepts only a single `SELECT`/`WITH` statement,
opens the database read-only and uses an SQLite authorizer that denies anything except reads.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
