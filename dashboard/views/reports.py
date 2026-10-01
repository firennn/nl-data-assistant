"""Weekly reports: generate a report for a chosen week and browse the saved ones.

Calls the report module's public entry point (reports.export.generate_report) on the Olist
demo database. Reports are written to reports/output/ (not committed).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from dashboard.context import AppContext
from dashboard.views import View
from reports.export import DEFAULT_WEEK_END, generate_report

FIRST_WEEK_END = date(2017, 1, 8)  # the data is complete from 2017-01-01 (docs/SCHEMA.md)


def saved_reports(folder: Path) -> list[Path]:
    """HTML reports in the output folder, newest week first."""
    if not folder.exists():
        return []
    return sorted(folder.glob("weekly_report_*.html"), reverse=True)


def week_of(path: Path) -> str:
    return path.stem.removeprefix("weekly_report_")


def _download(label: str, path: Path, mime: str) -> None:
    if path.exists():
        st.download_button(label, path.read_bytes(), file_name=path.name, mime=mime)


def render(ctx: AppContext) -> None:
    st.header("Weekly reports")
    st.caption(
        "Key metrics, trends, anomalies and a revenue forecast for one week of the Olist demo "
        "data. The summary is written from the computed numbers; if the LLM provider is not "
        "available, a plain summary is used instead."
    )

    with st.form("generate"):
        left, right = st.columns([2, 1])
        week_end = left.date_input(
            "Week ending on",
            value=DEFAULT_WEEK_END,
            min_value=FIRST_WEEK_END,
            max_value=DEFAULT_WEEK_END,
            help="A report week is the 7 days ending on this date. "
            "The last full week in the data ends on 2018-08-19.",
        )
        with_pdf = right.checkbox("Also create a PDF", value=False)
        submitted = st.form_submit_button("Generate report")
    if submitted:
        with st.spinner("Building the report..."):
            try:
                result = generate_report(
                    week_end, out_dir=ctx.reports_dir, db_path=ctx.demo.db_path, pdf=with_pdf
                )
            except Exception as exc:
                st.error(f"The report could not be created: {exc}")
            else:
                st.success(f"Report for the week ending {week_end} created.")
                ctx.state["report_week"] = week_of(result.html_path)

    reports = saved_reports(ctx.reports_dir)
    if not reports:
        st.info("No reports yet. Choose a week above and generate one.")
        return

    weeks = [week_of(p) for p in reports]
    current = ctx.state.get("report_week")
    index = weeks.index(current) if current in weeks else 0
    chosen = st.selectbox("Saved reports (week ending)", weeks, index=index)
    html_path = ctx.reports_dir / f"weekly_report_{chosen}.html"

    cols = st.columns(3)
    with cols[0]:
        _download("Download HTML", html_path, "text/html")
    with cols[1]:
        _download("Download PDF", html_path.with_suffix(".pdf"), "application/pdf")
    with cols[2]:
        _download("Download JSON", html_path.with_suffix(".json"), "application/json")
    components.html(html_path.read_text(encoding="utf-8"), height=900, scrolling=True)


VIEW = View(key="reports", title="Reports", icon="📄", order=20, render=render, olist_only=True)
