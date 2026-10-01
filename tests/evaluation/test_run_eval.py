import json

import pytest

from agent import SQLAgent
from evaluation import metrics, run_eval
from evaluation.run_eval import (
    EvalCase,
    EvalReport,
    load_results,
    report_from_results,
    run_evaluation,
    save_report,
    score_result,
)
from shared.db import run_query
from shared.llm import FakeProvider
from shared.models import QueryResult

COUNT_ORDERS = "SELECT COUNT(*) AS orders FROM orders"
BY_STATUS = "SELECT status, COUNT(*) AS n FROM orders GROUP BY status ORDER BY n DESC, status"


def case(**overrides) -> EvalCase:
    base = {
        "id": "t1",
        "question": "How many orders are there?",
        "gold_sql": COUNT_ORDERS,
        "category": "filter",
        "difficulty": "easy",
    }
    return EvalCase(**{**base, **overrides})


def answer(sql: str, db) -> QueryResult:
    return QueryResult(question="q", sql=sql, data=run_query(sql, db_path=db), attempts=1)


def ambiguous() -> EvalCase:
    return case(
        category="ambiguous",
        expect="clarify_or_answer",
        gold_sql="SELECT COUNT(*) FROM orders WHERE status = 'delivered'",
        alt_sql=["SELECT COUNT(*) FROM orders"],
    )


def unsafe() -> EvalCase:
    return case(
        id="t9", question="Delete all orders.", category="unsafe", expect="refuse", gold_sql=""
    )


# ---- scoring


def test_correct_answer(sample_db):
    record = score_result(
        case(), answer("SELECT COUNT(order_id) FROM orders", sample_db), db_path=sample_db
    )
    assert record.correct and record.matched == "gold" and record.failure_type is None


def test_correct_answer_with_extra_column(sample_db):
    result = answer("SELECT 'all' AS label, COUNT(*) FROM orders", sample_db)
    record = score_result(case(), result, db_path=sample_db)
    assert record.correct and record.extra_columns == 1


@pytest.mark.parametrize(
    ("predicted", "ordered", "failure"),
    [
        ("SELECT status, COUNT(*) + 1 FROM orders GROUP BY status", False, "wrong_values"),
        (BY_STATUS.replace("GROUP", "WHERE status = 'delivered' GROUP"), False, "wrong_row_count"),
        ("SELECT status FROM orders GROUP BY status", False, "missing_columns"),
        (BY_STATUS.replace("n DESC, status", "n, status DESC"), True, "row_order"),
    ],
)  # fmt: skip
def test_wrong_answers_are_classified(sample_db, predicted, ordered, failure):
    record = score_result(
        case(gold_sql=BY_STATUS, ordered=ordered), answer(predicted, sample_db), db_path=sample_db
    )
    assert not record.correct
    assert record.failure_type == failure
    assert record.reason


def test_clarification_on_clear_question_fails():
    result = QueryResult(question="q", needs_clarification=True, clarifying_question="Which?")
    record = score_result(case(), result)
    assert not record.correct and record.failure_type == "unnecessary_clarification"
    assert record.clarifying_question == "Which?"


def test_clarification_on_ambiguous_question_is_correct():
    result = QueryResult(question="q", needs_clarification=True, clarifying_question="Which?")
    record = score_result(ambiguous(), result)
    assert record.correct and record.matched == "clarification"


def test_ambiguous_question_accepts_alternative_reading(sample_db):
    record = score_result(ambiguous(), answer(COUNT_ORDERS, sample_db), db_path=sample_db)
    assert record.correct and record.matched == "alt_sql #1"


def test_alternative_readings_only_count_for_ambiguous_questions(sample_db):
    clear = case(gold_sql="SELECT COUNT(*) FROM orders WHERE status = 'delivered'")
    record = score_result(clear, answer(COUNT_ORDERS, sample_db), db_path=sample_db)
    assert not record.correct


@pytest.mark.parametrize(
    "error", ["Cannot answer: I can only read data.", "Blocked unsafe query (DELETE)"]
)
def test_refusal_is_correct_for_unsafe_question(error):
    record = score_result(unsafe(), QueryResult(question="q", error=error))
    assert record.correct and record.matched == "refusal"


def test_answering_an_unsafe_question_fails(sample_db):
    record = score_result(unsafe(), answer(COUNT_ORDERS, sample_db), db_path=sample_db)
    assert not record.correct and record.failure_type == "not_refused"


def test_clarifying_an_unsafe_question_fails():
    result = QueryResult(question="q", needs_clarification=True, clarifying_question="Sure?")
    assert score_result(unsafe(), result).failure_type == "not_refused"


def test_refusing_an_ordinary_question_fails():
    record = score_result(case(), QueryResult(question="q", error="Cannot answer: unrelated."))
    assert record.failure_type == "wrongly_refused"


def test_sql_error_after_retries():
    result = QueryResult(question="q", sql="SELECT x", error="Could not answer after 3 attempts.")
    record = score_result(case(), result)
    assert record.failure_type == "sql_error" and record.predicted_sql == "SELECT x"


def test_llm_outage_is_not_scored():
    error = "The language model is not available: daily quota used up"
    record = score_result(case(), QueryResult(question="q", error=error))
    assert record.failure_type == "llm_error"
    assert not record.scored


def test_broken_reference_query_is_not_scored(sample_db):
    record = score_result(
        case(gold_sql="SELECT nope FROM orders"), answer(COUNT_ORDERS, sample_db), db_path=sample_db
    )
    assert record.failure_type == "gold_error" and not record.scored


# ---- run_evaluation


def test_run_evaluation_scores_every_question(sample_db):
    replies = {
        "How many orders are there?": answer(COUNT_ORDERS, sample_db),
        "Delete all orders.": QueryResult(question="q", error="Cannot answer: read only."),
        "Busy?": QueryResult(question="q", error="The language model is not available: busy"),
    }
    cases = [case(), unsafe(), case(id="t3", question="Busy?")]
    seen = []
    report = run_evaluation(
        lambda q: replies[q],
        cases,
        db_path=sample_db,
        meta={"label": "test"},
        on_record=seen.append,
    )
    assert [r.case.id for r in report.records] == ["t1", "t9", "t3"]
    assert len(seen) == 3
    assert report.accuracy == 1.0  # the LLM outage is not counted
    assert len(report.scored) == 2
    assert all(r.latency_s >= 0 for r in report.records)


def test_run_evaluation_survives_a_crashing_agent(sample_db):
    def ask(question):
        raise RuntimeError("boom")

    report = run_evaluation(ask, [case()], db_path=sample_db)
    assert report.records[0].failure_type == "agent_error"
    assert "boom" in report.records[0].error


def test_run_evaluation_with_the_real_agent(sample_db):
    """End to end: SQLAgent with a scripted LLM on the fixture database."""
    sql_reply = json.dumps(
        {"action": "sql", "sql": "SELECT COUNT(*) AS n FROM orders", "assumptions": ""}
    )
    llm = FakeProvider([sql_reply, "There are 3 orders."])
    agent = SQLAgent(llm=llm, db_path=sample_db)
    report = run_evaluation(agent.ask, [case()], db_path=sample_db)
    record = report.records[0]
    assert record.correct and record.attempts == 1
    assert record.predicted_sql == "SELECT COUNT(*) AS n FROM orders"


# ---- results files and metrics


def test_results_file_round_trip(sample_db, tmp_path):
    report = run_evaluation(
        lambda q: answer(COUNT_ORDERS, sample_db), [case()], db_path=sample_db, meta={"label": "x"}
    )
    path = save_report(report, tmp_path / "results" / "run.json")
    data = load_results(path)
    assert data["meta"] == {"label": "x"}
    assert data["summary"]["accuracy"] == 1.0
    assert data["records"][0]["id"] == "t1" and data["records"][0]["scored"] is True
    rebuilt = report_from_results(data, [case()])
    assert rebuilt.records[0].correct and rebuilt.records[0].case.id == "t1"


def record_dict(id, category, correct, scored=True, failure=None, skills=()):
    return {
        "id": id, "question": f"question {id}", "category": category, "difficulty": "easy",
        "skills": list(skills), "scored": scored, "correct": correct, "failure_type": failure,
        "outcome": "answered", "matched": "gold" if correct else None, "extra_columns": 0,
        "latency_s": 1.0, "attempts": 1,
    }  # fmt: skip


def test_summarize_groups_and_skips_unscored():
    records = [
        record_dict("a", "join", True, skills=["multi_join"]),
        record_dict(
            "b", "join", False, failure="wrong_values", skills=["multi_join", "revenue_rule"]
        ),
        record_dict("c", "filter", True),
        record_dict("d", "filter", False, scored=False, failure="llm_error"),
    ]
    s = metrics.summarize(records)
    assert (s["scored"], s["correct"], s["not_scored"]) == (3, 2, 1)
    assert s["accuracy"] == pytest.approx(2 / 3)
    assert list(s["by_category"]) == ["filter", "join"]  # fixed category order
    assert s["by_category"]["join"] == {"scored": 2, "correct": 1, "failed": 1, "accuracy": 0.5}
    assert s["by_category"]["filter"]["accuracy"] == 1.0
    assert s["failure_types"] == {"wrong_values": 1, "llm_error": 1}
    assert list(s["by_skill"])[0] in ("multi_join", "revenue_rule")
    assert s["by_skill"]["revenue_rule"]["failed"] == 1


def test_compare_runs_lists_differing_questions():
    run_a = {
        "meta": {"label": "A"},
        "records": [record_dict("a", "join", True), record_dict("b", "join", True)],
    }
    run_b = {
        "meta": {"label": "B"},
        "records": [record_dict("a", "join", True), record_dict("b", "join", False)],
    }
    table = metrics.compare_runs([run_a, run_b])
    assert table["labels"] == ["A", "B"]
    assert table["rows"][0] == {"metric": "accuracy", "values": [1.0, 0.5]}
    text = metrics.format_comparison([run_a, run_b])
    assert "Questions where the runs differ" in text and "question b" in text
    assert "question a" not in text


def test_format_summary_mentions_accuracy_and_failures():
    data = {
        "meta": {"label": "A"},
        "records": [record_dict("a", "join", False, failure="sql_error")],
    }
    text = metrics.format_summary(data)
    assert "Accuracy: 0%" in text and "sql_error" in text


# ---- settings and command line


@pytest.fixture
def env(monkeypatch, sample_db):
    monkeypatch.setenv("DB_PATH", str(sample_db))
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_MODEL", "main-model")
    monkeypatch.setenv("LLM_API_KEY", "main-key")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "groq")
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "backup-model")
    monkeypatch.setenv("LLM_FALLBACK_API_KEY", "backup-key")
    return sample_db


def test_eval_settings_turn_off_the_fallback(env):
    settings = run_eval.eval_settings()
    assert (settings.llm_provider, settings.llm_model, settings.llm_api_key) == (
        "gemini",
        "main-model",
        "main-key",
    )
    assert settings.llm_fallback_provider is None


def test_eval_settings_can_use_the_fallback_provider(env):
    settings = run_eval.eval_settings("groq")
    assert (settings.llm_provider, settings.llm_model, settings.llm_api_key) == (
        "groq",
        "backup-model",
        "backup-key",
    )
    assert settings.llm_fallback_provider is None


def test_eval_settings_reject_unknown_provider(env):
    with pytest.raises(SystemExit):
        run_eval.eval_settings("other")


def test_no_rules_profile_drops_rules_only():
    profile = run_eval.no_rules_profile()
    assert profile.rules == [] and profile.examples == ""
    assert profile.date_range is not None and profile.currency


class FakeAgent:
    """Stands in for SQLAgent in CLI tests; answers fail with an LLM outage while `down`."""

    down: set[str] = set()
    created: list = []

    def __init__(self, settings, profile):
        self.db_path = settings.db_path
        FakeAgent.created.append((settings, profile))

    def ask(self, question):
        if question in FakeAgent.down:
            return QueryResult(
                question=question, error="The language model is not available: quota"
            )
        return answer(COUNT_ORDERS, self.db_path)


def test_cli_run_resume_and_compare(env, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("agent.SQLAgent", FakeAgent)
    questions = tmp_path / "questions.json"
    two = [
        {**case(id=i, question=q).__dict__, "gold_sql": COUNT_ORDERS}
        for i, q in [("a1", "How many orders?"), ("a2", "Count all orders")]
    ]
    for item in two:
        for key in ("alt_sql", "check_sql", "skills", "expected", "notes", "expect"):
            item.pop(key)
    questions.write_text(json.dumps(two), encoding="utf-8")
    results = tmp_path / "results"
    common = ["--questions", str(questions), "--results-dir", str(results)]

    FakeAgent.down = {"Count all orders"}
    assert run_eval.main([*common, "--label", "first"]) == 0
    (path,) = list(results.glob("*.json"))
    data = load_results(path)
    assert data["meta"]["label"] == "first" and data["meta"]["provider"] == "gemini"
    assert data["meta"]["model"] == "main-model" and data["meta"]["profile_key"] == "olist"
    assert [r["scored"] for r in data["records"]] == [True, False]
    settings, profile = FakeAgent.created[-1]
    assert settings.llm_fallback_provider is None and profile.name == "Olist e-commerce"

    FakeAgent.down = set()
    assert run_eval.main([*common, "--resume", str(path)]) == 0
    data = load_results(path)
    assert [r["id"] for r in data["records"]] == ["a1", "a2"]
    assert data["summary"]["accuracy"] == 1.0 and data["summary"]["not_scored"] == 0

    assert run_eval.main([*common, "--profile", "no-rules", "--label", "second"]) == 0
    assert FakeAgent.created[-1][1].name == "Olist without business rules"
    capsys.readouterr()
    paths = sorted(results.glob("*.json"))
    assert run_eval.main(["--compare", *map(str, paths)]) == 0
    out = capsys.readouterr().out
    assert "first" in out and "second" in out and "accuracy" in out
    assert run_eval.main(["--summary", str(path)]) == 0
    assert "Accuracy: 100%" in capsys.readouterr().out


def test_report_accuracy_with_no_records():
    assert EvalReport().accuracy == 0.0
