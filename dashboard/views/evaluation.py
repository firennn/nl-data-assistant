"""Evaluation results: accuracy, failure analysis, per-question details and model comparison.

Reads the results files written by `python -m evaluation.run_eval`; all numbers come from
evaluation/metrics.py, so they match the command line.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard.context import AppContext
from dashboard.views import FULL_WIDTH, View
from evaluation import metrics
from evaluation.run_eval import QUESTIONS_PATH, list_results, load_results, questions_version
from shared.charts import render_chart
from shared.models import ChartSpec

RESULT_LABELS = {True: "correct", False: "wrong", None: "not scored"}


@st.cache_data(show_spinner=False)
def _load(path: str, mtime: float) -> dict:  # mtime makes the cache reload changed files
    return load_results(path)


def load_run(path: Path) -> dict:
    return _load(str(path), path.stat().st_mtime)


def run_label(data: dict, path: Path | None = None) -> str:
    meta = data.get("meta", {})
    started = str(meta.get("started_at", "")).replace("T", " ")[:16]
    name = meta.get("label") or (path.stem if path else "run")
    return f"{name} ({meta.get('model', '?')}, {started})" if started else name


def group_frame(groups: dict[str, dict], name: str) -> pd.DataFrame:
    """Accuracy per group as a table: name, accuracy (%), correct, scored."""
    rows = [
        {
            name: key,
            "accuracy": None if g["accuracy"] is None else round(100 * g["accuracy"], 1),
            "correct": g["correct"],
            "scored": g["scored"],
        }
        for key, g in groups.items()
    ]
    return pd.DataFrame(rows, columns=[name, "accuracy", "correct", "scored"])


def records_frame(data: dict) -> pd.DataFrame:
    """One row per question for the results table."""
    rows = [
        {
            "id": r["id"],
            "result": RESULT_LABELS[r["correct"] if r["scored"] else None],
            "category": r["category"],
            "difficulty": r["difficulty"],
            "failure": r.get("failure_type") or "",
            "matched": r.get("matched") or "",
            "seconds": r.get("latency_s"),
            "question": r["question"],
        }
        for r in data.get("records", [])
    ]
    return pd.DataFrame(rows)


def _bar(df: pd.DataFrame, x: str, title: str) -> None:
    if df.empty:
        return
    spec = ChartSpec(kind="bar", title=title, x=x, y="accuracy")
    st.plotly_chart(render_chart(df, spec), **FULL_WIDTH)


def _show_run(data: dict) -> None:
    meta, summary = data.get("meta", {}), metrics.summarize(data.get("records", []))
    if meta.get("questions_version") and meta["questions_version"] != questions_version():
        st.warning(
            "This run used a different version of evaluation/questions.json; "
            "its numbers may not be comparable with newer runs."
        )

    cols = st.columns(4)
    accuracy = summary["accuracy"]
    cols[0].metric("Accuracy", "n/a" if accuracy is None else f"{accuracy:.0%}")
    cols[1].metric("Correct", f"{summary['correct']} / {summary['scored']}")
    cols[2].metric("Not scored", summary["not_scored"])
    cols[3].metric("Avg time per question", f"{summary['avg_latency_s'] or 0:.1f} s")
    st.caption(
        f"Provider: {meta.get('provider', '?')} · model: {meta.get('model', '?')} · "
        f"profile: {meta.get('profile', '?')} · "
        "not scored = the LLM provider was unavailable (left out of the accuracy)"
    )

    left, right = st.columns(2)
    with left:
        _bar(
            group_frame(summary["by_category"], "category"), "category", "Accuracy by category (%)"
        )
    with right:
        _bar(
            group_frame(summary["by_difficulty"], "difficulty"),
            "difficulty",
            "Accuracy by difficulty (%)",
        )

    st.subheader("Failure analysis")
    if not summary["failure_types"]:
        st.success("No failures in this run.")
    else:
        left, right = st.columns(2)
        failures = pd.DataFrame(
            sorted(summary["failure_types"].items(), key=lambda kv: -kv[1]),
            columns=["failure type", "questions"],
        )
        left.dataframe(failures, hide_index=True, **FULL_WIDTH)
        skills = pd.DataFrame(
            [
                {"skill": name, "failed": g["failed"], "scored": g["scored"]}
                for name, g in summary["by_skill"].items()
                if g["failed"]
            ]
        )
        if not skills.empty:
            right.dataframe(skills, hide_index=True, **FULL_WIDTH)
    st.caption(
        f"Ambiguous questions answered with a clarifying question: {summary['clarified']}; "
        f"with another valid reading: {summary['alt_reading']}."
    )

    st.subheader("Questions")
    table = records_frame(data)
    only_failures = st.toggle("Only failed questions", value=False)
    categories = st.multiselect("Categories", sorted(table["category"].unique()))
    shown = table
    if only_failures:
        shown = shown[shown["result"] == "wrong"]
    if categories:
        shown = shown[shown["category"].isin(categories)]
    st.dataframe(shown, hide_index=True, **FULL_WIDTH)

    by_id = {r["id"]: r for r in data.get("records", [])}
    if not shown.empty:
        chosen = st.selectbox("Question details", list(shown["id"]))
        record = by_id[chosen]
        st.markdown(f"**{record['id']}: {record['question']}**")
        st.write(f"Result: {RESULT_LABELS[record['correct'] if record['scored'] else None]}")
        if record.get("failure_type"):
            st.write(f"Failure type: `{record['failure_type']}` {record.get('reason') or ''}")
        if record.get("clarifying_question"):
            st.write(f"Clarifying question: {record['clarifying_question']}")
        if record.get("error"):
            st.write(f"Error: {record['error']}")
        left, right = st.columns(2)
        left.caption("Reference (gold) SQL")
        left.code(record.get("gold_sql") or "(no SQL: the request must be refused)", "sql")
        right.caption("SQL from the agent")
        right.code(record.get("predicted_sql") or "(none)", "sql")


def _show_comparison(paths: list[Path]) -> None:
    labels = {run_label(load_run(p), p): p for p in paths}
    chosen = st.multiselect("Runs to compare", list(labels), default=list(labels)[:3])
    if len(chosen) < 2:
        st.info("Choose at least two runs to compare.")
        return
    table = metrics.compare_runs([load_run(labels[c]) for c in chosen])
    columns = [load_run(labels[c])["meta"].get("label", c) for c in chosen]
    rows = [
        [row["metric"]] + ["n/a" if v is None else f"{v:.0%}" for v in row["values"]]
        for row in table["rows"]
    ]
    st.dataframe(pd.DataFrame(rows, columns=["", *columns]), hide_index=True)
    marks = {True: "correct", False: "wrong", None: "not scored"}
    differing = [
        [q["id"], *(marks[v] for v in q["results"]), q["question"]]
        for q in table["questions"]
        if len(set(q["results"])) > 1
    ]
    if differing:
        st.caption("Questions where the runs differ")
        frame = pd.DataFrame(differing, columns=["id", *columns, "question"])
        st.dataframe(frame, hide_index=True, **FULL_WIDTH)
    else:
        st.caption("The runs got the same questions right and wrong.")


def render(ctx: AppContext) -> None:
    st.header("Evaluation")
    st.caption(
        f"Held-out questions with verified answers ({QUESTIONS_PATH.name}), asked to the agent "
        "on the Olist database. A prediction counts as correct when its data matches."
    )
    paths = list_results(ctx.results_dir)
    if not paths:
        st.info(
            "No evaluation results yet. Run `python -m evaluation.run_eval` to create them "
            "(results are saved in evaluation/results/)."
        )
        return

    single, compare = st.tabs(["One run", "Compare runs"])
    with single:
        labels = {run_label(load_run(p), p): p for p in paths}
        chosen = st.selectbox("Run", list(labels))
        _show_run(load_run(labels[chosen]))
    with compare:
        _show_comparison(paths)


VIEW = View(
    key="evaluation", title="Evaluation", icon="📊", order=30, render=render, olist_only=True
)
