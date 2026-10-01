"""PDF version of the weekly report (owner: owner2), drawn with matplotlib.

Three A4 pages: (1) summary and key metrics, (2) the charts, (3) week-over-week changes,
anomalies and the forecast. Charts are redrawn from the same ChartSpec and data as the HTML
report, so both show the same numbers. matplotlib writes the PDF itself, so no browser or
other tool is needed (works on Windows and in CI).
"""

from __future__ import annotations

import math
import textwrap
from pathlib import Path

from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

from reports.charts import ReportChart
from reports.summary import format_number
from reports.weekly_report import WeeklyReport

A4 = (8.27, 11.69)  # inches, portrait
WRAP = 95  # characters per line for paragraphs
TEXT_SIZE = 9


def write_pdf(report: WeeklyReport, path: Path, notes: dict[str, str]) -> Path:
    """Write the report to `path`. `notes` holds the plain-text method and forecast notes
    (keys: "summary", "anomalies", "changes", "forecast") shared with the HTML report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    title = f"Weekly report: {report.week_start} to {report.week_end}"
    with PdfPages(path, metadata={"Title": title}) as pdf:
        for page in (_summary_page, _charts_page, _details_page):
            fig = Figure(figsize=A4)
            page(fig, report, notes, title)
            pdf.savefig(fig)
    return path


def _summary_page(fig: Figure, report: WeeklyReport, notes: dict[str, str], title: str) -> None:
    fig.text(0.07, 0.95, title, fontsize=16, weight="bold")
    y = _paragraph(fig, 0.91, "Summary", report.summary)
    if notes.get("summary"):
        y = _paragraph(fig, y, None, notes["summary"], color="#66717f")
    rows = []
    for m in report.metrics:
        change = m.change_pct
        change_text = "n/a" if change is None or math.isnan(change) else f"{change:+.1f}%"
        rows.append(
            [
                m.name,
                format_number(m.value, m.unit),
                format_number(m.comparison_value, m.unit),
                change_text,
            ]
        )
    _table(fig, y - 0.02, "Key metrics", ["Metric", "This week", "Previous week", "Change"], rows)


def _charts_page(fig: Figure, report: WeeklyReport, notes: dict[str, str], title: str) -> None:
    fig.text(0.07, 0.95, "Charts", fontsize=14, weight="bold")
    charts = report.charts or []
    if not charts:
        fig.text(0.07, 0.9, "No charts for this week.", fontsize=TEXT_SIZE)
        return
    height = 0.84 / len(charts)
    for i, chart in enumerate(charts):
        bottom = 0.9 - (i + 1) * height + 0.05
        ax = fig.add_axes((0.25, bottom, 0.68, height - 0.09))
        _draw_chart(ax, chart)


def _details_page(fig: Figure, report: WeeklyReport, notes: dict[str, str], title: str) -> None:
    fig.text(0.07, 0.95, "Changes, anomalies and forecast", fontsize=14, weight="bold")
    y = 0.91
    changes = report.metric_changes
    if changes is None or changes.empty:
        y = _paragraph(fig, y, "Week-over-week changes", notes.get("changes", ""))
    else:
        rows = [
            [
                r.metric,
                format_number(r.value, r.unit),
                format_number(r.previous, r.unit),
                f"{r.change_pct:+.1f}%",
            ]
            for r in changes.itertuples()
        ]
        y = _table(
            fig,
            y,
            "Week-over-week changes",
            ["Metric", "This week", "Previous week", "Change"],
            rows,
        )

    anomalies = report.anomalies
    if anomalies is None or anomalies.empty:
        y = _paragraph(fig, y, "Anomalies", "No daily anomalies in revenue or orders.")
    else:
        rows = [
            [
                str(r.date),
                r.metric,
                format_number(r.value),
                format_number(r.expected),
                f"{r.z_score:.1f}",
            ]
            for r in anomalies.itertuples()
        ]
        y = _table(fig, y, "Anomalies", ["Date", "Metric", "Value", "Expected", "z-score"], rows)
    y = _paragraph(fig, y, None, notes.get("anomalies", ""), color="#66717f")

    forecast = report.forecast
    if forecast is None or forecast.empty:
        _paragraph(fig, y, "Revenue forecast", "No forecast is available for this week.")
        return
    rows = [
        [
            str(r.week),
            format_number(r.forecast, "BRL"),
            format_number(r.lower, "BRL"),
            format_number(r.upper, "BRL"),
        ]
        for r in forecast.itertuples()
    ]
    y = _table(
        fig, y, "Revenue forecast", ["Week starting", "Forecast", "80% range from", "to"], rows
    )
    _paragraph(fig, y, None, notes.get("forecast", ""), color="#66717f")


def _paragraph(fig: Figure, y: float, heading: str | None, text: str, color: str = "black"):
    """Draw an optional heading and wrapped text starting at height `y`; return the next y."""
    if heading:
        fig.text(0.07, y, heading, fontsize=12, weight="bold")
        y -= 0.03
    lines = textwrap.wrap(text, WRAP) if text else []
    for line in lines:
        fig.text(0.07, y, line, fontsize=TEXT_SIZE, color=color)
        y -= 0.018
    return y - 0.015


def _table(fig: Figure, y: float, heading: str, columns: list[str], rows: list[list[str]]):
    """Draw a heading and a table whose top is at `y`; return the y below it."""
    fig.text(0.07, y, heading, fontsize=12, weight="bold")
    row_height = 0.022
    height = row_height * (len(rows) + 1)
    top = y - 0.015
    ax = fig.add_axes((0.07, top - height, 0.86, height))
    ax.axis("off")
    table = ax.table(
        cellText=rows,
        colLabels=columns,
        loc="upper left",
        cellLoc="right",
        colLoc="right",
        bbox=(0, 0, 1, 1),
    )
    table.auto_set_font_size(False)
    table.set_fontsize(TEXT_SIZE)
    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor("#d9dde3")
        if col == 0:
            cell.set_text_props(ha="left")
        if row == 0:
            cell.set_text_props(weight="bold")
            cell.set_facecolor("#eef1f5")
    return top - height - 0.04


def _draw_chart(ax, chart: ReportChart) -> None:
    spec, data = chart.spec, chart.data
    ax.set_title(chart.title, fontsize=11, loc="left")
    if data.empty or spec.x not in data or spec.y not in data:
        ax.axis("off")
        ax.text(0.0, 0.5, "No data for this week.", fontsize=TEXT_SIZE)
        return
    thousands = FuncFormatter(lambda v, _: f"{v:,.0f}")
    if spec.kind == "line":
        ax.plot(data[spec.x], data[spec.y], marker="o", color="#3b6fb6")
        ax.yaxis.set_major_formatter(thousands)
        ax.tick_params(axis="x", labelrotation=45, labelsize=8)
        ax.set_ylabel(spec.y, fontsize=8)
    else:
        # Horizontal bars keep long category names readable; largest at the top.
        ax.barh(data[spec.x].astype(str)[::-1], data[spec.y][::-1], color="#3b6fb6")
        ax.xaxis.set_major_formatter(thousands)
        ax.set_xlabel(spec.y, fontsize=8)
        ax.tick_params(axis="y", labelsize=8)
    ax.tick_params(labelsize=8)
    ax.grid(axis="both", color="#e4e7eb", linewidth=0.5)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
