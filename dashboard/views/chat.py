"""Chat with the data: ask a question, see the answer, the SQL used, the table and the chart.

Uses the current data source from the context, so the same page works for the Olist demo and,
later, for uploaded databases. Each data source keeps its own conversation.
"""

from __future__ import annotations

import time

import streamlit as st

from dashboard.context import AppContext
from dashboard.usage import log_question
from dashboard.views import FULL_WIDTH, View
from shared.charts import render_chart
from shared.models import QueryResult

CHAT_KEY = "chat_messages"
EXAMPLE_QUESTIONS = (
    "Which 5 states have the most customers?",
    "What is the average review score per month in 2018?",
    "How many orders were paid by credit card?",
)


def history_for_agent(messages: list[dict]) -> list[tuple[str, str]]:
    """Earlier turns as (role, text) pairs, so replies to clarifying questions are understood."""
    history = []
    for m in messages:
        if m["role"] == "user":
            history.append(("user", m["text"]))
        else:
            result: QueryResult = m["result"]
            text = result.clarifying_question or result.explanation or result.error or ""
            history.append(("assistant", text))
    return history


def show_result(result: QueryResult) -> None:
    """Render one answer: explanation, SQL, table and chart, or the clarifying question/error."""
    if result.needs_clarification:
        st.write(result.clarifying_question)
        st.caption("Reply below to answer the question.")
        return
    if result.error:
        st.error(result.error)
        if result.sql:
            with st.expander("SQL that was tried"):
                st.code(result.sql, "sql")
        return
    if result.explanation:
        st.write(result.explanation)
    if result.sql:
        with st.expander("SQL used", expanded=False):
            st.code(result.sql, "sql")
    data = result.data
    if data is None:
        return
    chart = result.chart
    if chart and chart.kind == "metric" and not data.empty:
        value = data.iloc[0, 0]
        st.metric(
            chart.title or str(data.columns[0]), f"{value:,}" if isinstance(value, int) else value
        )
    elif chart and chart.kind != "table" and not data.empty:
        st.plotly_chart(render_chart(data, chart), **FULL_WIDTH)
    st.dataframe(data, hide_index=True, **FULL_WIDTH)
    if data.attrs.get("truncated"):
        st.caption("Only the first rows are shown; the result was cut at the row limit.")


def render(ctx: AppContext) -> None:
    source = ctx.source
    st.header("Chat with the data")
    st.caption(
        f"Database: {source.name}. Ask a business question in plain language; the answer shows "
        "the SQL that was run. The assistant can only read data."
    )

    conversations = ctx.state.setdefault(CHAT_KEY, {})
    messages: list[dict] = conversations.setdefault(source.key, [])

    if not messages and source.is_demo:
        st.caption("Examples: " + " · ".join(EXAMPLE_QUESTIONS))
    if messages and st.button("Clear conversation"):
        messages.clear()

    for m in messages:
        with st.chat_message(m["role"]):
            if m["role"] == "user":
                st.write(m["text"])
            else:
                show_result(m["result"])

    question = st.chat_input("Ask a question about the data")
    if not question:
        return

    with st.chat_message("user"):
        st.write(question)
    history = history_for_agent(messages)
    with st.chat_message("assistant"):
        with st.spinner("Working on it..."):
            start = time.perf_counter()
            try:
                result = ctx.agent(source).ask(question, history=history or None)
            except Exception as exc:  # the page must not crash on an unexpected agent error
                result = QueryResult(question=question, error=f"Something went wrong: {exc}")
            latency = time.perf_counter() - start
        show_result(result)

    messages.append({"role": "user", "text": question})
    messages.append({"role": "assistant", "result": result})
    try:
        log_question(
            ctx.usage_log,
            result=result,
            source_key=source.key,
            source_kind=source.kind,
            latency_s=latency,
        )
    except OSError:
        pass  # usage stats are optional; never block an answer on them


VIEW = View(key="chat", title="Chat", icon="💬", order=10, render=render)
