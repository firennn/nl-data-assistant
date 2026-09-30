"""Export a WeeklyReport to HTML and PDF (owner: owner2). Output goes to reports/output/."""

from __future__ import annotations

from pathlib import Path

from reports.weekly_report import WeeklyReport

OUTPUT_DIR = Path(__file__).resolve().parent / "output"


def export_html(report: WeeklyReport, path: Path | None = None) -> Path:
    """Write the report as a single self-contained HTML file and return its path.

    TODO(owner2):
    - Default path: OUTPUT_DIR / f"weekly_report_{report.week_end}.html".
    - Render charts with shared.charts.render_chart(...).to_html(include_plotlyjs="cdn").
    - Sections: summary, KPI table (value, previous week, change %), charts, anomalies, forecast.
    """
    raise NotImplementedError


def export_pdf(report: WeeklyReport, path: Path | None = None) -> Path:
    """Write the report as a PDF and return its path.

    TODO(owner2):
    - Pick the lightest option that works on Windows and in CI (for example, static chart
      images plus a simple PDF layout) and record the choice in docs/DECISIONS.md.
    """
    raise NotImplementedError


def run_weekly(week_end: str | None = None) -> Path:
    """Entry point for the scheduled job: build the report and export it.

    TODO(owner2):
    - Parse week_end (YYYY-MM-DD); default to the last full week in the dataset
      (2018-08-19, see docs/SCHEMA.md "Known data limitations").
    - Provide `python -m reports.export --week-end 2018-08-19` and document how to schedule it
      (Windows Task Scheduler / cron / GitHub Actions schedule).
    """
    raise NotImplementedError
