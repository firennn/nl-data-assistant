from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from dashboard import usage
from dashboard.context import (
    DataSource,
    add_source,
    available_sources,
    get_context,
    set_current_source,
)
from dashboard.views import View, discover, get_view
from dashboard.views.chat import history_for_agent
from dashboard.views.evaluation import group_frame, records_frame, run_label
from dashboard.views.reports import saved_reports, week_of
from shared.models import DatasetProfile, QueryResult
from shared.profiles import OLIST_PROFILE

# ---- page registry


def test_discover_finds_every_page_once():
    views = discover()
    keys = [v.key for v in views]
    assert keys == ["chat", "upload", "reports", "evaluation", "usage"]
    assert all(isinstance(v, View) and callable(v.render) for v in views)


def test_olist_only_pages():
    assert {v.key for v in discover() if v.olist_only} == {"evaluation"}


def test_get_view_unknown_key():
    with pytest.raises(KeyError):
        get_view("nope")


# ---- current data source


def test_default_source_is_the_olist_demo():
    ctx = get_context({})
    assert ctx.source.key == "olist" and ctx.source.is_demo
    assert ctx.source.profile is OLIST_PROFILE
    assert ctx.source.db_path == ctx.settings.db_path
    assert ctx.demo == ctx.source


def test_added_source_becomes_current_and_demo_stays(tmp_path):
    state: dict = {}
    ctx = get_context(state)
    upload = DataSource(
        "up1", "sales.csv", tmp_path / "up.db", DatasetProfile("Uploaded"), "upload"
    )
    add_source(state, upload, ctx.settings)
    ctx = get_context(state)
    assert ctx.source.key == "up1" and not ctx.source.is_demo
    assert ctx.demo.key == "olist"
    assert list(available_sources(state, ctx.settings)) == ["olist", "up1"]
    set_current_source(state, "olist")
    assert get_context(state).source.key == "olist"


def test_unknown_current_source_falls_back_to_demo():
    assert get_context({"current_source": "gone"}).source.key == "olist"


def test_agent_is_created_once_per_source():
    created = []

    def factory(source, settings):
        created.append(source.key)
        return object()

    state = {"agent_factory": factory}
    ctx = get_context(state)
    assert ctx.agent() is ctx.agent()
    assert created == ["olist"]


def test_overrides_for_paths(tmp_path):
    ctx = get_context({"results_dir": str(tmp_path / "r"), "usage_log": str(tmp_path / "u.jsonl")})
    assert ctx.results_dir == tmp_path / "r" and ctx.usage_log == tmp_path / "u.jsonl"


# ---- usage log


def test_usage_log_round_trip(tmp_path):
    path = tmp_path / "usage.jsonl"
    ok = QueryResult(question="How many?", sql="SELECT 1", data=pd.DataFrame({"n": [1]}))
    usage.log_question(path, result=ok, source_key="olist", source_kind="demo", latency_s=1.234)
    clarify = QueryResult(question="Revenue?", needs_clarification=True, clarifying_question="?")
    when = datetime(2026, 1, 2, 10, 0)
    usage.log_question(
        path, result=clarify, source_key="up1", source_kind="upload", latency_s=2, now=when
    )
    path.open("a", encoding="utf-8").write("not json\n")
    log = usage.read_log(path)
    assert list(log["outcome"]) == ["answered", "clarification"]
    assert log["question"].tolist()[0] == "How many?"
    assert pd.isna(log["question"].tolist()[1])  # uploaded data: question not stored
    summary = usage.usage_summary(log)
    assert summary["questions"] == 2 and summary["answered_pct"] == 0.5
    assert summary["days"] == 2


def test_usage_summary_of_empty_log(tmp_path):
    assert usage.usage_summary(usage.read_log(tmp_path / "none.jsonl"))["questions"] == 0


# ---- page helpers


def test_chat_history_for_agent():
    messages = [
        {"role": "user", "text": "Revenue?"},
        {"role": "assistant", "result": QueryResult("Revenue?", needs_clarification=True,
                                                    clarifying_question="Which year?")},
        {"role": "user", "text": "2017"},
        {"role": "assistant", "result": QueryResult("2017", error="Cannot answer: no.")},
    ]  # fmt: skip
    assert history_for_agent(messages) == [
        ("user", "Revenue?"),
        ("assistant", "Which year?"),
        ("user", "2017"),
        ("assistant", "Cannot answer: no."),
    ]


def test_evaluation_helpers():
    data = {
        "meta": {"label": "run-a", "model": "m1", "started_at": "2026-10-02T03:42:47"},
        "records": [
            {"id": "q1", "question": "Q?", "category": "join", "difficulty": "easy",
             "scored": True, "correct": False, "failure_type": "wrong_values", "matched": None,
             "latency_s": 1.0},
            {"id": "q2", "question": "R?", "category": "join", "difficulty": "easy",
             "scored": False, "correct": False, "failure_type": "llm_error", "matched": None,
             "latency_s": 1.0},
        ],
    }  # fmt: skip
    assert run_label(data) == "run-a (m1, 2026-10-02 03:42)"
    frame = records_frame(data)
    assert frame["result"].tolist() == ["wrong", "not scored"]
    groups = group_frame({"join": {"accuracy": 0.5, "correct": 1, "scored": 2}}, "category")
    assert groups.to_dict("records") == [
        {"category": "join", "accuracy": 50.0, "correct": 1, "scored": 2}
    ]


def test_saved_reports_newest_first(tmp_path):
    for week in ("2018-08-12", "2018-08-19"):
        (tmp_path / f"weekly_report_{week}.html").write_text("x", encoding="utf-8")
    (tmp_path / "other.html").write_text("x", encoding="utf-8")
    paths = saved_reports(tmp_path)
    assert [week_of(p) for p in paths] == ["2018-08-19", "2018-08-12"]
    assert saved_reports(Path(tmp_path / "missing")) == []
