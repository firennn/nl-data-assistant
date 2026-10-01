from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from dashboard import app
from evaluation.run_eval import EvalCase, run_evaluation, save_report
from shared.models import ChartSpec, QueryResult

APP = str(Path(app.__file__).resolve())


def test_public_interface_exists():
    for fn in ("main", "chat_tab", "reports_tab", "evaluation_tab"):
        assert callable(getattr(app, fn))


class FakeAgent:
    """Answers every question with a fixed result and remembers what it was asked."""

    def __init__(self, result: QueryResult):
        self.result = result
        self.calls = []

    def ask(self, question, history=None):
        self.calls.append((question, history))
        return QueryResult(**{**self.result.__dict__, "question": question})


def answered() -> QueryResult:
    return QueryResult(
        question="q",
        sql="SELECT state, COUNT(*) AS customers FROM customers GROUP BY state",
        data=pd.DataFrame({"state": ["SP", "RJ"], "customers": [41746, 12852]}),
        chart=ChartSpec(kind="bar", title="Customers by state", x="state", y="customers"),
        explanation="Sao Paulo has the most customers.",
        attempts=1,
    )


def make_app(tmp_path, agent=None) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["results_dir"] = str(tmp_path / "results")
    at.session_state["reports_dir"] = str(tmp_path / "reports")
    at.session_state["usage_log"] = str(tmp_path / "usage.jsonl")
    if agent is not None:
        at.session_state["agent_factory"] = lambda source, settings: agent
    return at


def go_to(at: AppTest, title: str) -> AppTest:
    radio = at.sidebar.radio[0]
    label = next(option for option in radio.options if option.endswith(title))
    return radio.set_value(label).run()


def test_app_starts_with_all_pages(tmp_path):
    at = make_app(tmp_path).run()
    assert not at.exception
    options = at.sidebar.radio[0].options
    assert [o.split(" ", 1)[-1] for o in options] == ["Chat", "Reports", "Evaluation", "Usage"]
    assert at.sidebar.selectbox[0].value == "olist"


def test_chat_tab(tmp_path):
    """The chat page shows the explanation, the SQL, the table and the chart."""
    agent = FakeAgent(answered())
    at = make_app(tmp_path, agent).run()
    at.chat_input[0].set_value("Which states have the most customers?").run()
    assert not at.exception
    assert agent.calls == [("Which states have the most customers?", None)]
    assert any("Sao Paulo has the most customers." in m.value for m in at.markdown)
    assert any("SELECT state" in c.value for c in at.code)
    assert at.dataframe[0].value["customers"].tolist() == [41746, 12852]
    assert len(at.get("plotly_chart")) == 1
    assert (tmp_path / "usage.jsonl").exists()


def test_chat_passes_history_after_clarification(tmp_path):
    agent = FakeAgent(
        QueryResult(question="q", needs_clarification=True, clarifying_question="Which year?")
    )
    at = make_app(tmp_path, agent).run()
    at.chat_input[0].set_value("Revenue?").run()
    assert any("Which year?" in m.value for m in at.markdown)
    at.chat_input[0].set_value("2017").run()
    assert agent.calls[1] == ("2017", [("user", "Revenue?"), ("assistant", "Which year?")])


def test_chat_shows_errors_without_crashing(tmp_path):
    agent = FakeAgent(QueryResult(question="q", error="Cannot answer: I can only read data."))
    at = make_app(tmp_path, agent).run()
    at.chat_input[0].set_value("Delete everything").run()
    assert not at.exception
    assert at.error[0].value == "Cannot answer: I can only read data."


def test_evaluation_page_without_results(tmp_path):
    at = go_to(make_app(tmp_path).run(), "Evaluation")
    assert not at.exception
    assert "No evaluation results yet" in at.info[0].value


def write_run(folder: Path, sample_db, label: str, correct: bool) -> None:
    case = EvalCase(
        id="q1",
        question="How many orders?",
        gold_sql="SELECT COUNT(*) FROM orders",
        category="filter",
        difficulty="easy",
    )
    sql = "SELECT COUNT(*) FROM orders" if correct else "SELECT COUNT(*) + 1 FROM orders"
    from shared.db import run_query

    def ask(question):
        return QueryResult(question=question, sql=sql, data=run_query(sql, db_path=sample_db))

    report = run_evaluation(ask, [case], db_path=sample_db, meta={"label": label, "model": "m"})
    save_report(report, folder / f"{label}.json")


def test_evaluation_page_shows_a_run(tmp_path, sample_db):
    write_run(tmp_path / "results", sample_db, "run-a", correct=True)
    at = go_to(make_app(tmp_path).run(), "Evaluation")
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Accuracy"] == "100%"
    assert metrics["Correct"] == "1 / 1"


def test_evaluation_page_compares_runs(tmp_path, sample_db):
    write_run(tmp_path / "results", sample_db, "run-a", correct=True)
    write_run(tmp_path / "results", sample_db, "run-b", correct=False)
    at = go_to(make_app(tmp_path).run(), "Evaluation")
    assert not at.exception
    frames = [d.value for d in at.dataframe]
    assert any("run-a" in f.columns and "run-b" in f.columns for f in frames)


def test_reports_page_lists_saved_reports(tmp_path):
    folder = tmp_path / "reports"
    folder.mkdir()
    (folder / "weekly_report_2018-08-19.html").write_text("<h1>Report</h1>", encoding="utf-8")
    (folder / "weekly_report_2018-08-12.html").write_text("<h1>Older</h1>", encoding="utf-8")
    at = go_to(make_app(tmp_path).run(), "Reports")
    assert not at.exception
    assert at.selectbox[0].options == ["2018-08-19", "2018-08-12"]


def test_reports_page_without_reports(tmp_path):
    at = go_to(make_app(tmp_path).run(), "Reports")
    assert not at.exception
    assert "No reports yet" in at.info[0].value


def test_usage_page_after_a_question(tmp_path):
    at = make_app(tmp_path, FakeAgent(answered())).run()
    at.chat_input[0].set_value("Which states have the most customers?").run()
    at = go_to(at, "Usage")
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Questions"] == "1" and metrics["Answered"] == "100%"


def test_usage_page_without_log(tmp_path):
    at = go_to(make_app(tmp_path).run(), "Usage")
    assert "No questions asked yet" in at.info[0].value


@pytest.mark.parametrize("fn", ["chat_tab", "reports_tab", "evaluation_tab"])
def test_tab_functions_render_their_page(monkeypatch, fn):
    rendered = []
    monkeypatch.setattr(app, "render_view", lambda view, ctx: rendered.append(view.key))
    monkeypatch.setattr(app, "get_context", lambda: None)
    getattr(app, fn)()
    assert rendered == [fn.removesuffix("_tab")]
