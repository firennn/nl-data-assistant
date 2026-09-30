"""Streamlit dashboard (owner: owner3). Run with: streamlit run dashboard/app.py

Uses only public entry points: agent.SQLAgent.ask, reports.weekly_report.build_report /
reports.export, evaluation results files, and shared.charts.render_chart.
"""

from __future__ import annotations

import streamlit as st


def chat_tab() -> None:
    """Chat with the data.

    TODO(owner3):
    - Keep the conversation in st.session_state; call SQLAgent().ask(question, history).
    - For each answer show: explanation, the SQL used (st.code), the result table and the
      chart (st.plotly_chart(render_chart(result.data, result.chart))).
    - If result.needs_clarification, show the clarifying question and pass the reply as history.
    - Show result.error clearly without a stack trace.
    """
    st.info("Chat is not implemented yet.")


def reports_tab() -> None:
    """Browse and generate weekly reports.

    TODO(owner3):
    - List files in reports/output/, preview the selected HTML report (st.components.v1.html).
    - Button to build a report for a chosen week_end date.
    """
    st.info("Reports are not implemented yet.")


def evaluation_tab() -> None:
    """Show evaluation results.

    TODO(owner3):
    - Load the latest results file from evaluation/results/.
    - Show overall accuracy, accuracy by category/difficulty, and a table of failures with
      the question, gold SQL, predicted SQL and failure type.
    """
    st.info("Evaluation results are not implemented yet.")


def main() -> None:
    st.set_page_config(page_title="NL Data Assistant", layout="wide")
    st.title("NL Data Assistant")
    chat, reports, evaluation = st.tabs(["Chat", "Reports", "Evaluation"])
    with chat:
        chat_tab()
    with reports:
        reports_tab()
    with evaluation:
        evaluation_tab()


if __name__ == "__main__":
    main()
