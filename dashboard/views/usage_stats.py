"""Simple usage statistics from the local usage log written by the chat page."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.context import AppContext
from dashboard.usage import read_log, usage_summary
from dashboard.views import FULL_WIDTH, View
from shared.charts import render_chart
from shared.models import ChartSpec


def render(ctx: AppContext) -> None:
    st.header("Usage")
    st.caption(
        "Questions asked in the chat on this computer (stored locally in "
        f"{ctx.usage_log.name}, never committed). For uploaded databases only the outcome is "
        "stored, not the question."
    )
    log = read_log(ctx.usage_log)
    if log.empty:
        st.info("No questions asked yet. Ask something on the Chat page.")
        return

    s = usage_summary(log)
    cols = st.columns(4)
    cols[0].metric("Questions", s["questions"])
    cols[1].metric("Answered", f"{s['answered_pct']:.0%}")
    cols[2].metric("Avg answer time", f"{s['avg_latency_s']:.1f} s")
    cols[3].metric("Days used", s["days"])

    per_day = log.groupby(log["time"].dt.date.astype(str)).size().reset_index(name="questions")
    per_day.columns = ["day", "questions"]
    st.plotly_chart(
        render_chart(
            per_day, ChartSpec(kind="bar", title="Questions per day", x="day", y="questions")
        ),
        **FULL_WIDTH,
    )

    left, right = st.columns(2)
    outcomes = log["outcome"].value_counts().rename_axis("outcome").reset_index(name="questions")
    left.dataframe(outcomes, hide_index=True, **FULL_WIDTH)
    charts = (
        log["chart"].fillna("none").value_counts().rename_axis("chart").reset_index(name="answers")
    )
    right.dataframe(charts, hide_index=True, **FULL_WIDTH)

    st.subheader("Recent questions")
    recent = log.sort_values("time", ascending=False).head(20)
    recent = recent.assign(question=recent["question"].fillna("(uploaded data, not stored)"))
    st.dataframe(
        pd.DataFrame(recent[["time", "question", "outcome", "latency_s"]]),
        hide_index=True,
        **FULL_WIDTH,
    )


VIEW = View(key="usage", title="Usage", icon="📈", order=40, render=render, needs_database=False)
