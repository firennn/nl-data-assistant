"""Streamlit dashboard (owner: owner3). Run with: streamlit run dashboard/app.py

Pages live in dashboard/views/ (one file per page) and are found automatically. The current
data source (database + dataset profile) is chosen in one place, dashboard/context.py, and
passed to every page. Uses only public entry points: agent.SQLAgent.ask,
reports.export.generate_report, the evaluation results files and shared.charts.render_chart.
"""

from __future__ import annotations

import sys
from pathlib import Path

# `streamlit run dashboard/app.py` puts dashboard/ on the import path, not the project root.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from dashboard.context import (  # noqa: E402
    AppContext,
    available_sources,
    get_context,
    set_current_source,
)
from dashboard.views import View, discover, get_view  # noqa: E402

PAGE_KEY = "page"


def render_view(view: View, ctx: AppContext) -> None:
    if view.olist_only and not ctx.source.is_demo:
        st.info(
            f"This page always uses the Olist demo database; the selected database "
            f"({ctx.source.name}) is used on the Chat page."
        )
    view.render(ctx)


def _render(key: str) -> None:
    render_view(get_view(key), get_context())


def chat_tab() -> None:
    """Chat with the data (dashboard/views/chat.py)."""
    _render("chat")


def reports_tab() -> None:
    """Browse and generate weekly reports (dashboard/views/reports.py)."""
    _render("reports")


def evaluation_tab() -> None:
    """Evaluation results (dashboard/views/evaluation.py)."""
    _render("evaluation")


def sidebar(ctx: AppContext, views: list[View]) -> View:
    """Database picker and page menu; returns the selected page."""
    with st.sidebar:
        st.title("NL Data Assistant")
        sources = available_sources(ctx.state, ctx.settings)
        keys = list(sources)
        chosen = st.selectbox(
            "Database",
            keys,
            index=keys.index(ctx.source.key),
            format_func=lambda k: sources[k].name,
            help="The database the Chat page answers questions about.",
        )
        if chosen != ctx.source.key:
            set_current_source(ctx.state, chosen)
            st.rerun()
        labels = {f"{v.icon} {v.title}".strip(): v for v in views}
        selected = st.radio("Page", list(labels), key=PAGE_KEY)
        st.caption("Read-only: the assistant never changes the data.")
    return labels[selected]


def main() -> None:
    st.set_page_config(page_title="NL Data Assistant", layout="wide")
    ctx = get_context()
    views = discover()
    render_view(sidebar(ctx, views), ctx)


if __name__ == "__main__":
    main()
