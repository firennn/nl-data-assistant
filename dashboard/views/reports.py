"""Weekly reports: generate a report for a chosen week and browse the saved ones.

For the Olist demo the report reads its fixed tables and is written to reports/output/ (not
committed). For uploaded data the user first says which columns hold the date, the amount and
optionally the order, customer and category (reports.sales.SalesMapping, pre-filled from the
column names); those reports are stored with the upload and deleted with it.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from dashboard.context import AppContext
from dashboard.views import View
from dashboard.views.upload import upload_label, upload_reports_dir
from reports.export import DEFAULT_WEEK_END, generate_report
from reports.sales import (
    MappingError,
    SalesMapping,
    check_mapping,
    describe_tables,
    suggest_mapping,
)

FIRST_WEEK_END = date(2017, 1, 8)  # the data is complete from 2017-01-01 (docs/SCHEMA.md)
MAPPINGS_KEY = "report_mappings"  # database path -> SalesMapping chosen on this page
TABLES_KEY = "report_tables"  # database path -> describe_tables result (read once)
WEEK_KEY = "report_week"
NONE = "(none)"


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
    if ctx.source.is_demo:
        _render_demo(ctx)
    else:
        _render_upload(ctx)


# --- Olist demo --------------------------------------------------------------------------------


def _render_demo(ctx: AppContext) -> None:
    st.caption(
        "Key metrics, trends, anomalies and a revenue forecast for one week of the Olist demo "
        "data. The summary is written from the computed numbers; if the LLM provider is not "
        "available, a plain summary is used instead. To report on your own data, upload it on "
        "the Upload data page."
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
        _generate(
            ctx,
            week_end,
            out_dir=ctx.reports_dir,
            db_path=ctx.demo.db_path,
            pdf=with_pdf,
        )
    _browse(ctx, ctx.reports_dir)


# --- uploaded data -----------------------------------------------------------------------------


def _render_upload(ctx: AppContext) -> None:
    source = ctx.source
    label = upload_label(ctx)
    st.caption(
        f"Key metrics, trends, anomalies and a revenue forecast for one week of {label}. "
        "Choose which columns hold the date and the amount; the other columns are optional."
    )
    key = str(source.db_path)
    tables = ctx.state.setdefault(TABLES_KEY, {})
    if key not in tables:
        try:
            tables[key] = describe_tables(source.db_path)
        except Exception as exc:
            st.error(f"The uploaded data could not be read: {exc}")
            return
    usable = {n: t for n, t in tables[key].items() if t.date_columns and t.numeric_columns}
    if not usable:
        st.warning(
            "A weekly report needs a table with a date column and a numeric amount column "
            "(for example order date and sales). None of the uploaded tables has both; you can "
            "still ask questions about the data on the Chat page."
        )
        return

    mappings = ctx.state.setdefault(MAPPINGS_KEY, {})
    current = mappings.get(key) or suggest_mapping(usable)
    mapping = _mapping_form(usable, current, widget_prefix=_short(key))
    try:
        first, last = check_mapping(mapping, source.db_path)
    except MappingError as exc:
        st.error(str(exc))
        return
    mappings[key] = mapping
    st.caption(f"Dates in {mapping.date_column}: {first} to {last}.")

    with st.form("generate_upload"):
        left, right = st.columns([2, 1])
        week_end = left.date_input(
            "Week ending on",
            value=last,
            min_value=min(first + timedelta(days=6), last),
            max_value=last,
            help="A report week is the 7 days ending on this date, compared with the 7 days "
            "before. The default is the last date in the data; if that day is not complete "
            "yet, choose an earlier one.",
        )
        with_pdf = right.checkbox("Also create a PDF", value=False)
        submitted = st.form_submit_button("Generate report")
    out_dir = upload_reports_dir(ctx)
    if submitted:
        _generate(
            ctx,
            week_end,
            out_dir=out_dir,
            db_path=source.db_path,
            pdf=with_pdf,
            mapping=mapping,
            dataset=label,
        )
    _browse(ctx, out_dir)


def _mapping_form(tables: dict, current: SalesMapping, *, widget_prefix: str) -> SalesMapping:
    """Widgets for the column mapping, pre-filled with `current`; returns the chosen mapping."""
    names = list(tables)
    with st.expander("Columns used for the report", expanded=True):
        table_name = st.selectbox(
            "Table",
            names,
            index=_index(names, current.table),
            key=f"{widget_prefix}_table",
        )
        table = tables[table_name]
        if table_name != current.table:
            current = suggest_mapping({table_name: table}, currency=current.currency) or current
        left, right = st.columns(2)
        date_column = left.selectbox(
            "Date",
            table.date_columns,
            index=_index(table.date_columns, current.date_column),
            key=f"{widget_prefix}_{table_name}_date",
        )
        amount_column = right.selectbox(
            "Amount (revenue per row)",
            table.numeric_columns,
            index=_index(table.numeric_columns, current.amount_column),
            key=f"{widget_prefix}_{table_name}_amount",
        )
        optional = [NONE, *table.columns]
        cols = st.columns(3)
        picks = [
            cols[i].selectbox(
                label,
                optional,
                index=_index(optional, getattr(current, attr) or NONE),
                key=f"{widget_prefix}_{table_name}_{attr}",
                help=help_text,
            )
            for i, (label, attr, help_text) in enumerate(
                [
                    ("Order id", "order_column", "Rows with the same id are one order."),
                    ("Customer", "customer_column", "Adds customer and new-customer counts."),
                    ("Category", "category_column", "Adds a chart of the top categories."),
                ]
            )
        ]
        currency = st.text_input(
            "Currency label",
            value=current.currency,
            max_chars=12,
            placeholder="e.g. USD, EUR, IDR",
            key=f"{widget_prefix}_currency",
        )
    return replace(
        current,
        table=table_name,
        date_column=date_column,
        amount_column=amount_column,
        order_column=None if picks[0] == NONE else picks[0],
        customer_column=None if picks[1] == NONE else picks[1],
        category_column=None if picks[2] == NONE else picks[2],
        currency=currency.strip(),
    )


def _index(options: list[str], value: str | None) -> int:
    return options.index(value) if value in options else 0


def _short(text: str) -> str:
    """Widget keys per database, so a new upload does not inherit the old choices."""
    return "map_" + hashlib.sha1(text.encode()).hexdigest()[:8]


# --- shared ------------------------------------------------------------------------------------


def _generate(ctx: AppContext, week_end: date, **kwargs) -> None:
    with st.spinner("Building the report..."):
        try:
            result = generate_report(week_end, **kwargs)
        except Exception as exc:
            st.error(f"The report could not be created: {exc}")
        else:
            st.success(f"Report for the week ending {week_end} created.")
            ctx.state[WEEK_KEY] = week_of(result.html_path)


def _browse(ctx: AppContext, folder: Path) -> None:
    reports = saved_reports(folder)
    if not reports:
        st.info("No reports yet. Choose a week above and generate one.")
        return

    weeks = [week_of(p) for p in reports]
    current = ctx.state.get(WEEK_KEY)
    index = weeks.index(current) if current in weeks else 0
    chosen = st.selectbox("Saved reports (week ending)", weeks, index=index)
    html_path = folder / f"weekly_report_{chosen}.html"

    cols = st.columns(3)
    with cols[0]:
        _download("Download HTML", html_path, "text/html")
    with cols[1]:
        _download("Download PDF", html_path.with_suffix(".pdf"), "application/pdf")
    with cols[2]:
        _download("Download JSON", html_path.with_suffix(".json"), "application/json")
    components.html(html_path.read_text(encoding="utf-8"), height=900, scrolling=True)


VIEW = View(key="reports", title="Reports", icon="📄", order=20, render=render)
