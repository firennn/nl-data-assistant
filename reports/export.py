"""Export a WeeklyReport to HTML and PDF (owner: owner2). Output goes to reports/output/.

    python -m reports.export                         # last full week in the data (2018-08-19)
    python -m reports.export --week-end 2018-08-19 --out reports/output
    python -m reports.export --pdf                   # also write the PDF version

Scheduling examples (cron, Windows Task Scheduler, GitHub Actions) are in reports/README.md.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import date
from html import escape
from pathlib import Path

import pandas as pd

from reports.anomalies import AnomalyConfig
from reports.sales import SalesMapping, check_mapping
from reports.summary import format_number
from reports.weekly_report import WeeklyReport, build_report
from shared.charts import render_chart
from shared.llm import LLMProvider

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
DEFAULT_WEEK_END = date(2018, 8, 19)  # last full week in the data (docs/SCHEMA.md)

STYLE = """
body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; margin: 0 auto;
       max-width: 960px; padding: 24px 16px; color: #1f2933; background: #fafafa; }
h1 { margin-bottom: 4px; } h2 { margin-top: 32px; border-bottom: 1px solid #d9dde3; }
.muted { color: #66717f; font-size: 0.9em; }
.summary { background: #fff; border-left: 4px solid #3b6fb6; padding: 12px 16px; }
table { border-collapse: collapse; width: 100%; background: #fff; }
th, td { padding: 6px 10px; border-bottom: 1px solid #e4e7eb; text-align: right; }
th:first-child, td:first-child { text-align: left; }
.up { color: #1d7a45; } .down { color: #b3261e; }
.table-wrap { overflow-x: auto; }
"""


@dataclass
class ReportResult:
    """What generate_report produced: file paths plus a JSON-ready summary."""

    html_path: Path
    json_path: Path
    summary: dict
    pdf_path: Path | None = None


def export_html(report: WeeklyReport, path: Path | None = None) -> Path:
    """Write the report as a single HTML file (Plotly loaded from its CDN) and return its path.

    Sections: summary, KPI table (value, previous week, change %), charts, week-over-week
    changes, anomalies and forecast.
    """
    path = Path(path) if path else OUTPUT_DIR / f"weekly_report_{report.week_end}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(
        [
            f"<h1>Weekly report: {report.week_start} to {report.week_end}</h1>",
            f'<p class="muted">{escape(report.dataset)}</p>' if report.dataset else "",
            _summary_html(report),
            _kpi_html(report),
            _charts_html(report),
            _changes_html(report),
            _anomalies_html(report),
            _forecast_html(report),
        ]
    )
    title = f"Weekly report {report.week_end}"
    page = (
        f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title><style>{STYLE}</style></head>"
        f"<body>\n{body}\n</body></html>\n"
    )
    path.write_text(page, encoding="utf-8")
    return path


def export_pdf(report: WeeklyReport, path: Path | None = None) -> Path:
    """Write the report as a three-page A4 PDF (summary and metrics, charts, details) and
    return its path. Drawn with matplotlib; see reports/pdf.py."""
    from reports.pdf import write_pdf  # matplotlib is only loaded when a PDF is requested

    path = Path(path) if path else OUTPUT_DIR / f"weekly_report_{report.week_end}.pdf"
    return write_pdf(report, path, report_notes(report))


def report_to_dict(report: WeeklyReport) -> dict:
    """A JSON-ready summary of the report, for the dashboard or other tools."""
    forecast = report.forecast
    return {
        "week_start": report.week_start.isoformat(),
        "week_end": report.week_end.isoformat(),
        "summary": report.summary,
        "summary_source": report.summary_source,
        "dataset": report.dataset,
        "currency": report.currency,
        "metrics": [
            {
                "name": m.name,
                "value": _clean(m.value),
                "previous": _clean(m.comparison_value),
                "change_pct": _clean(m.change_pct),
                "unit": m.unit,
            }
            for m in report.metrics
        ],
        "metric_changes": _records(report.metric_changes),
        "anomalies": _records(report.anomalies),
        "forecast": None
        if forecast is None
        else {
            "model": forecast.attrs.get("model"),
            "beats_baseline": forecast.attrs.get("beats_baseline"),
            "weeks": _records(forecast),
        },
    }


def generate_report(
    week_end: date | str | None = None,
    *,
    out_dir: Path | None = None,
    db_path: str | Path | None = None,
    llm: LLMProvider | None = None,
    config: AnomalyConfig | None = None,
    pdf: bool = False,
    mapping: SalesMapping | None = None,
    dataset: str = "",
) -> ReportResult:
    """Build the report for the week ending on `week_end`, write the HTML file and a JSON
    summary next to it (and the PDF if `pdf`), and return the paths and the summary.

    Without `mapping` the report covers the Olist tables and `week_end` defaults to the last
    full week in that data. With a reports.sales.SalesMapping it covers the mapped table and
    defaults to the 7 days ending on the last date in it. `dataset` names the data in the
    report title."""
    if week_end is None and mapping is not None:
        week = check_mapping(mapping, db_path)[1]
    else:
        week = _parse_week_end(week_end)
    report = build_report(
        week, db_path=db_path, llm=llm, config=config, mapping=mapping, dataset=dataset
    )
    out_dir = Path(out_dir) if out_dir else OUTPUT_DIR
    html_path = export_html(report, out_dir / f"weekly_report_{report.week_end}.html")
    summary = report_to_dict(report)
    json_path = out_dir / f"weekly_report_{report.week_end}.json"
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    pdf_path = export_pdf(report, out_dir / f"weekly_report_{report.week_end}.pdf") if pdf else None
    return ReportResult(
        html_path=html_path, json_path=json_path, summary=summary, pdf_path=pdf_path
    )


def run_weekly(
    week_end: str | None = None,
    *,
    out_dir: Path | None = None,
    db_path: str | Path | None = None,
    llm: LLMProvider | None = None,
    pdf: bool = False,
) -> Path:
    """Entry point for the scheduled job: build the report, export it, return the HTML path."""
    result = generate_report(week_end, out_dir=out_dir, db_path=db_path, llm=llm, pdf=pdf)
    return result.html_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build and export the weekly report.")
    parser.add_argument(
        "--week-end",
        default=None,
        help=f"last day of the report week, YYYY-MM-DD (default {DEFAULT_WEEK_END})",
    )
    parser.add_argument("--out", type=Path, default=None, help="output folder")
    parser.add_argument("--db", type=Path, default=None, help="database file (default DB_PATH)")
    parser.add_argument("--pdf", action="store_true", help="also write the PDF version")
    args = parser.parse_args(argv)
    try:
        result = generate_report(args.week_end, out_dir=args.out, db_path=args.db, pdf=args.pdf)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Report for {result.summary['week_start']} to {result.summary['week_end']}")
    print(f"  HTML: {result.html_path}")
    print(f"  JSON: {result.json_path}")
    if result.pdf_path:
        print(f"  PDF:  {result.pdf_path}")
    print(f"  Summary: {result.summary['summary_source']}")


def _parse_week_end(value: date | str | None) -> date:
    if value is None:
        return DEFAULT_WEEK_END
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"week_end must be YYYY-MM-DD, got {value!r}.") from exc


def report_notes(report: WeeklyReport) -> dict[str, str]:
    """Plain-text notes on the summary, methods and forecast, shared by the HTML and PDF."""
    config = AnomalyConfig()
    notes = {
        "summary": "",
        "changes": f"No metric changed by {config.change_threshold_pct:g}% or more compared "
        "with the previous week.",
        "anomalies": f"Each day is compared with the average of the {config.daily_window} days "
        f"before it; a day is flagged when it is at least {config.z_threshold:g} standard "
        "deviations away (z-score). Weekday patterns are not modeled, and a day after a large "
        "spike has a raised baseline.",
        "forecast": "",
    }
    if report.summary_source == "fallback":
        notes["summary"] = (
            "The LLM summary was not available or used a number that is not in the report, "
            "so this summary lists the computed facts."
        )
    forecast = report.forecast
    if forecast is not None and not forecast.empty:
        model = forecast.attrs.get("model", "")
        scores = forecast.attrs.get("scores") or {}
        if not scores:
            notes["forecast"] = (
                "There is too little history to test a model, so the forecast repeats last week."
            )
        elif forecast.attrs.get("beats_baseline"):
            notes["forecast"] = (
                f"Model: {model}. In backtesting it beat the naive baseline (last week's value): "
                f"average error {format_number(scores[model]['mae'], report.currency)} vs "
                f"{format_number(scores['naive']['mae'], report.currency)}."
            )
        else:
            notes["forecast"] = (
                "The forecast model did not beat the naive baseline in backtesting, so the naive "
                "forecast (last week's revenue) is shown."
            )
    return notes


def _summary_html(report: WeeklyReport) -> str:
    note = report_notes(report)["summary"]
    note_html = f'<p class="muted">{escape(note)}</p>' if note else ""
    return (
        f'<h2>Summary</h2>\n<div class="summary"><p>{escape(report.summary)}</p></div>{note_html}'
    )


def _kpi_html(report: WeeklyReport) -> str:
    rows = []
    for m in report.metrics:
        change = m.change_pct
        if change is None or math.isnan(change):
            change_cell = "<td>n/a</td>"
        else:
            css = "up" if change > 0 else "down" if change < 0 else ""
            change_cell = f'<td class="{css}">{change:+.1f}%</td>'
        rows.append(
            f"<tr><td>{escape(m.name)}</td><td>{format_number(m.value, m.unit)}</td>"
            f"<td>{format_number(m.comparison_value, m.unit)}</td>{change_cell}</tr>"
        )
    return (
        "<h2>Key metrics</h2>\n"
        '<div class="table-wrap"><table><tr><th>Metric</th><th>This week</th>'
        "<th>Previous week</th><th>Change</th></tr>\n" + "\n".join(rows) + "</table></div>"
    )


def _charts_html(report: WeeklyReport) -> str:
    parts = ["<h2>Charts</h2>"]
    for i, chart in enumerate(report.charts):
        fig = render_chart(chart.data, chart.spec)
        parts.append(fig.to_html(full_html=False, include_plotlyjs="cdn" if i == 0 else False))
    return "\n".join(parts)


def _changes_html(report: WeeklyReport) -> str:
    changes = report.metric_changes
    if changes is None or changes.empty:
        note = escape(report_notes(report)["changes"])
        return f"<h2>Week-over-week changes</h2>\n<p>{note}</p>"
    rows = "\n".join(
        f"<tr><td>{escape(r.metric)}</td><td>{format_number(r.value, r.unit)}</td>"
        f"<td>{format_number(r.previous, r.unit)}</td><td>{r.change_pct:+.1f}%</td></tr>"
        for r in changes.itertuples()
    )
    return (
        "<h2>Week-over-week changes</h2>\n"
        '<div class="table-wrap"><table><tr><th>Metric</th><th>This week</th>'
        f"<th>Previous week</th><th>Change</th></tr>\n{rows}</table></div>"
    )


def _anomalies_html(report: WeeklyReport) -> str:
    method = f'<p class="muted">{escape(report_notes(report)["anomalies"])}</p>'
    if report.anomalies is None or report.anomalies.empty:
        return f"<h2>Anomalies</h2>\n<p>No daily anomalies in revenue or orders.</p>{method}"
    rows = "\n".join(
        f"<tr><td>{r.date}</td><td>{escape(r.metric)}</td><td>{format_number(r.value)}</td>"
        f"<td>{format_number(r.expected)}</td><td>{r.z_score:.1f}</td></tr>"
        for r in report.anomalies.itertuples()
    )
    return (
        "<h2>Anomalies</h2>\n"
        '<div class="table-wrap"><table><tr><th>Date</th><th>Metric</th><th>Value</th>'
        f"<th>Expected</th><th>z-score</th></tr>\n{rows}</table></div>{method}"
    )


def _forecast_html(report: WeeklyReport) -> str:
    forecast = report.forecast
    if forecast is None or forecast.empty:
        return "<h2>Revenue forecast</h2>\n<p>No forecast is available for this week.</p>"
    rows = "\n".join(
        f"<tr><td>{r.week}</td><td>{format_number(r.forecast, report.currency)}</td>"
        f"<td>{format_number(r.lower, report.currency)}</td>"
        f"<td>{format_number(r.upper, report.currency)}</td></tr>"
        for r in forecast.itertuples()
    )
    note = escape(report_notes(report)["forecast"])
    return (
        "<h2>Revenue forecast</h2>\n"
        '<div class="table-wrap"><table><tr><th>Week starting</th><th>Forecast</th>'
        f"<th>80% range from</th><th>to</th></tr>\n{rows}</table></div>"
        f'<p class="muted">{note}</p>'
    )


def _clean(value: object) -> object:
    """Turn NaN into None and numpy numbers into plain Python numbers for JSON."""
    if value is None:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _records(df: pd.DataFrame | None) -> list[dict]:
    if df is None or df.empty:
        return []
    return [
        {k: (v.isoformat() if isinstance(v, date) else _clean(v)) for k, v in row.items()}
        for row in df.to_dict(orient="records")
    ]


if __name__ == "__main__":
    main()
