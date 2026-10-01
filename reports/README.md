# Weekly report

Builds a weekly business report from the database: key metrics compared with the previous
week, week-over-week changes, daily anomalies, charts, a 4-week revenue forecast and a short
executive summary.

## Run it

```bash
python -m reports.export                         # last full week in the data (2018-08-19)
python -m reports.export --week-end 2018-08-12   # any other week (the 7 days ending that day)
python -m reports.export --out some/folder --db data/olist.db
python -m reports.export --pdf                   # also write the PDF version
```

Each run writes two files to `reports/output/` (not tracked by git):

- `weekly_report_<week_end>.html`: the report (charts load Plotly from its CDN)
- `weekly_report_<week_end>.json`: the same results as data, for the dashboard or other tools
- `weekly_report_<week_end>.pdf` (with `--pdf`): a three-page A4 version of the report

From Python:

```python
from reports.export import generate_report

result = generate_report("2018-08-19", pdf=True)
result.html_path, result.json_path, result.pdf_path, result.summary
```

The summary is written by the LLM configured in `.env`. Without an API key, or if the summary
contains a number that is not in the report, the report uses a plain summary of the computed
facts instead (shown as `summary_source: "fallback"`).

## Extend it

- New metric: add a function with `@register_metric` in `reports/metrics.py`.
- New daily series or anomaly detector: `@register_series` / `@register_detector` in
  `reports/anomalies.py`. Thresholds are in `AnomalyConfig`.
- New chart: add a function with `@register_chart` in `reports/charts.py`.

## Schedule it

The dataset ends in 2018, so a scheduled run always reports the same week unless
`--week-end` is passed. With live data, pass the last Sunday (or drop the default).

**cron (Linux/macOS)**, every Monday at 07:00:

```
0 7 * * 1  cd /path/to/nl-data-assistant && .venv/bin/python -m reports.export >> reports/output/cron.log 2>&1
```

**Windows Task Scheduler**, every Monday at 07:00 (run once in a terminal):

```
schtasks /Create /TN "Weekly report" /SC WEEKLY /D MON /ST 07:00 /TR "cmd /c cd /d C:\path\to\nl-data-assistant && .venv\Scripts\python -m reports.export"
```

**GitHub Actions** (example; to use it, save it under `.github/workflows/` and add the API
key as a repository secret named `LLM_API_KEY`):

```yaml
name: Weekly report
on:
  schedule:
    - cron: "0 7 * * 1"   # Mondays 07:00 UTC
  workflow_dispatch:
jobs:
  report:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: python -m pip install -r requirements.txt
      - run: python -m data.build_db
      - run: python -m reports.export
        env:
          LLM_API_KEY: ${{ secrets.LLM_API_KEY }}
      - uses: actions/upload-artifact@v4
        with:
          name: weekly-report
          path: reports/output/
```
