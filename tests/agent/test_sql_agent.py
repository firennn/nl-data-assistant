"""SQLAgent tests with a scripted LLM (FakeProvider) and the fixture database."""

import json

import pytest

from agent.sql_agent import ReplyFormatError, SQLAgent, parse_reply
from shared.config import Settings
from shared.db import QueryTimeoutError, run_query
from shared.llm import FakeProvider, LLMError, LLMRateLimitError
from shared.models import DatasetProfile
from shared.profiles import OLIST_PROFILE
from shared.schema import SCHEMA_MAX_CHARS


def sql_reply(sql: str, assumptions: str = "") -> str:
    return json.dumps({"action": "sql", "sql": sql, "assumptions": assumptions})


def clarify_reply(question: str) -> str:
    return json.dumps({"action": "clarify", "question": question})


def make_agent(sample_db, replies, max_retries: int = 2):
    llm = FakeProvider(replies)
    settings = Settings(db_path=sample_db, agent_max_retries=max_retries)
    return SQLAgent(llm=llm, settings=settings), llm


# --- parse_reply ---------------------------------------------------------------------------


def test_parse_reply_plain_and_fenced():
    assert parse_reply(sql_reply("SELECT 1"))["sql"] == "SELECT 1"
    fenced = "```json\n" + sql_reply("SELECT 2", "none") + "\n```"
    assert parse_reply(fenced) == {"action": "sql", "sql": "SELECT 2", "assumptions": "none"}
    assert parse_reply(clarify_reply("Which year?")) == {
        "action": "clarify",
        "question": "Which year?",
    }


@pytest.mark.parametrize(
    "text",
    [
        "",
        "SELECT 1",
        "{not json}",
        '{"action": "sql"}',
        '{"action": "clarify"}',
        '{"action": "dance", "sql": "SELECT 1"}',
    ],
)
def test_parse_reply_rejects_bad_replies(text):
    with pytest.raises(ReplyFormatError):
        parse_reply(text)


# --- ask -----------------------------------------------------------------------------------


def test_happy_path_returns_full_query_result(sample_db):
    agent, llm = make_agent(
        sample_db,
        [
            sql_reply("SELECT COUNT(*) AS delivered_orders FROM orders WHERE status = 'delivered'"),
            "There are 2 delivered orders.",
        ],
    )
    result = agent.ask("How many orders were delivered?")

    assert result.ok
    assert result.sql.startswith("SELECT COUNT(*)")
    assert result.data["delivered_orders"].iloc[0] == 2
    assert result.chart.kind == "metric"
    assert result.explanation == "There are 2 delivered orders."
    assert result.attempts == 1
    assert len(llm.calls) == 2  # SQL generation + explanation


def test_prompt_contains_schema_rules_and_question(sample_db):
    agent, llm = make_agent(sample_db, [sql_reply("SELECT 1 AS x"), "ok"])
    agent.ask("How many sellers are there?")
    prompt, system = llm.calls[0]
    assert "TABLE orders" in prompt and "FK -> customers.customer_id" in prompt
    assert "Question: How many sellers are there?" in prompt
    assert "read-only SELECT" in system and "Revenue" in system


def test_explanation_prompt_contains_sql_and_result(sample_db):
    agent, llm = make_agent(sample_db, [sql_reply("SELECT 42 AS answer", "test"), "ok"])
    agent.ask("What is the answer?")
    explain_prompt, _ = llm.calls[1]
    assert "SELECT 42 AS answer" in explain_prompt
    assert "42" in explain_prompt and "Assumptions made: test" in explain_prompt


def test_clarifying_question(sample_db):
    agent, llm = make_agent(sample_db, [clarify_reply("Best by revenue or by review score?")])
    result = agent.ask("Who are the best sellers?")
    assert result.needs_clarification
    assert result.clarifying_question == "Best by revenue or by review score?"
    assert not result.ok and result.sql is None
    assert len(llm.calls) == 1


def test_history_is_passed_to_the_prompt(sample_db):
    agent, llm = make_agent(sample_db, [sql_reply("SELECT 1 AS x"), "ok"])
    history = [("user", "Who are the best sellers?"), ("assistant", "By revenue or score?")]
    agent.ask("By revenue", history=history)
    prompt, _ = llm.calls[0]
    assert "User: Who are the best sellers?" in prompt
    assert "Assistant: By revenue or score?" in prompt


def test_retry_with_error_message_after_bad_sql(sample_db):
    agent, llm = make_agent(
        sample_db,
        [
            sql_reply("SELECT revenue FROM orders"),
            sql_reply("SELECT ROUND(SUM(price), 2) AS revenue FROM order_items"),
            "Revenue is 441.7 BRL.",
        ],
    )
    result = agent.ask("Total item revenue?")
    assert result.ok
    assert result.attempts == 2
    assert result.data["revenue"].iloc[0] == pytest.approx(441.7)
    retry_prompt, _ = llm.calls[1]
    assert "SELECT revenue FROM orders" in retry_prompt
    assert "no such column: revenue" in retry_prompt


def test_retry_cap_returns_error(sample_db):
    bad = sql_reply("SELECT nope FROM orders")
    agent, llm = make_agent(sample_db, [bad, bad, bad, "unused"], max_retries=2)
    result = agent.ask("Something impossible")
    assert not result.ok
    assert result.attempts == 3
    assert "Could not answer after 3 attempts" in result.error
    assert "no such column" in result.error
    assert result.sql == "SELECT nope FROM orders"
    assert len(llm.calls) == 3  # no explanation call for a failed answer


def test_invalid_reply_format_is_retried(sample_db):
    agent, llm = make_agent(
        sample_db, ["I think you want SELECT 1", sql_reply("SELECT 1 AS x"), "ok"]
    )
    result = agent.ask("Anything")
    assert result.ok and result.attempts == 2
    assert "did not contain a JSON object" in llm.calls[1][0]


def test_clarify_after_a_failed_attempt_is_not_accepted(sample_db):
    agent, _ = make_agent(
        sample_db,
        [
            sql_reply("SELECT nope FROM orders"),
            clarify_reply("Which?"),
            sql_reply("SELECT 1 AS x"),
            "ok",
        ],
    )
    result = agent.ask("Anything")
    assert result.ok and result.attempts == 3


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE orders",
        "DELETE FROM orders",
        "UPDATE orders SET status = 'x'",
        "SELECT 1; DELETE FROM orders",
        "INSERT INTO orders VALUES ('x')",
        "ATTACH DATABASE 'x.db' AS x",
        "PRAGMA writable_schema = 1",
        "SELECT 1 /* hidden */; DROP TABLE orders",
    ],
)
def test_unsafe_sql_is_blocked_and_never_run(sample_db, sql):
    agent, llm = make_agent(sample_db, [sql_reply(sql), sql_reply("SELECT 1 AS x"), "ok"])
    result = agent.ask("Please clean up the orders table")
    assert not result.ok
    assert "Blocked unsafe query" in result.error and "only read data" in result.error
    assert result.sql == sql
    assert len(llm.calls) == 1  # not retried
    assert len(run_query("SELECT * FROM orders", db_path=sample_db)) == 3  # data untouched


def test_llm_failure_is_reported(sample_db):
    agent, _ = make_agent(sample_db, [LLMRateLimitError("quota exceeded")])
    result = agent.ask("How many orders?")
    assert not result.ok
    assert "not available" in result.error and "quota exceeded" in result.error


def test_explanation_failure_still_returns_the_answer(sample_db):
    agent, _ = make_agent(
        sample_db, [sql_reply("SELECT COUNT(*) AS n FROM orders", "all statuses"), LLMError("x")]
    )
    result = agent.ask("How many orders?")
    assert result.ok
    assert result.explanation == "The query returned 1 row(s). Assumptions: all statuses"


def test_empty_question(sample_db):
    agent, llm = make_agent(sample_db, [])
    result = agent.ask("   ")
    assert result.error == "Please ask a question."
    assert llm.calls == []


def test_missing_database_is_reported(tmp_path):
    agent = SQLAgent(llm=FakeProvider([]), settings=Settings(db_path=tmp_path / "missing.db"))
    result = agent.ask("How many orders?")
    assert not result.ok and "data.build_db" in result.error


def test_row_limit_marks_result_as_truncated(sample_db):
    llm = FakeProvider([sql_reply("SELECT * FROM order_items"), "ok"])
    agent = SQLAgent(llm=llm, settings=Settings(db_path=sample_db), max_rows=2)
    result = agent.ask("Show all items")
    assert len(result.data) == 2 and result.data.attrs["truncated"] is True


def test_schema_is_described_once_per_agent(sample_db, monkeypatch):
    calls = []
    import agent.sql_agent as module

    real = module.describe_schema
    monkeypatch.setattr(
        module, "describe_schema", lambda path, **kw: calls.append(path) or real(path, **kw)
    )
    agent, _ = make_agent(sample_db, [sql_reply("SELECT 1 AS x"), "ok"] * 2)
    agent.ask("one")
    agent.ask("two")
    assert len(calls) == 1


def test_default_profile_is_olist_with_samples(sample_db):
    agent, llm = make_agent(sample_db, [sql_reply("SELECT 1 AS x"), "ok"])
    assert agent.profile is OLIST_PROFILE
    agent.ask("anything")
    (prompt, system), (_, explain_system) = llm.calls
    assert "Olist" in system and "Revenue" in system
    assert "order_items oi" in prompt  # Olist examples
    assert "samples:" in prompt
    assert "excluding canceled and unavailable orders" in explain_system


def test_generic_profile_has_no_olist_rules_and_no_samples(sample_db):
    llm = FakeProvider([sql_reply("SELECT 1 AS x"), "ok"])
    profile = DatasetProfile(name="Uploaded file")
    agent = SQLAgent(llm=llm, settings=Settings(db_path=sample_db), profile=profile)
    agent.ask("anything")
    (prompt, system), (_, explain_system) = llm.calls
    assert "TABLE orders" in prompt  # the schema is still there
    assert "samples:" not in prompt
    assert 'made-up table "sales"' in prompt
    for text in (system, explain_system):
        assert "Olist" not in text and "R$" not in text and "2018" not in text
    assert "relative to the latest date" in system


def test_samples_are_not_read_when_the_profile_excludes_them(sample_db, monkeypatch):
    import agent.sql_agent as module

    seen = {}
    real = module.describe_schema

    def spy(path, **kw):
        seen.update(kw)
        return real(path, **kw)

    monkeypatch.setattr(module, "describe_schema", spy)
    agent = SQLAgent(
        llm=FakeProvider([]),
        settings=Settings(db_path=sample_db),
        profile=DatasetProfile(name="x"),
    )
    agent.schema_text()
    assert seen["sample_values"] == 0


def test_file_that_is_not_a_database_is_reported(tmp_path):
    fake = tmp_path / "upload.sqlite"
    fake.write_text("name,age\nana,31\n")
    llm = FakeProvider([])
    agent = SQLAgent(llm=llm, settings=Settings(db_path=fake))
    result = agent.ask("How many rows?")
    assert not result.ok
    assert result.error == "Could not read the database: file is not a database."
    assert llm.calls == []


def test_schema_timeout_is_reported(sample_db, monkeypatch):
    import agent.sql_agent as module

    def slow(*_args, **_kwargs):
        raise QueryTimeoutError("Reading the database structure took longer than 10s.")

    monkeypatch.setattr(module, "describe_schema", slow)
    agent, llm = make_agent(sample_db, [])
    result = agent.ask("How many orders?")
    assert not result.ok and "took longer than 10s" in result.error
    assert llm.calls == []


def test_agent_limits_the_schema_text(sample_db, monkeypatch):
    import agent.sql_agent as module

    seen = {}
    real = module.schema_to_prompt

    def spy(schema, **kw):
        seen.update(kw)
        return real(schema, **kw)

    monkeypatch.setattr(module, "schema_to_prompt", spy)
    agent, _ = make_agent(sample_db, [])
    agent.schema_text()
    assert seen["max_chars"] == SCHEMA_MAX_CHARS == 12_000


def test_parse_reply_refuse():
    assert parse_reply('{"action": "refuse", "reason": "Read only."}') == {
        "action": "refuse",
        "reason": "Read only.",
    }
    assert parse_reply('{"action": "refuse"}')["reason"]


def test_refusal_is_returned_as_error_without_running_sql(sample_db):
    reply = json.dumps({"action": "refuse", "reason": "I can only read data."})
    agent, llm = make_agent(sample_db, [reply])
    result = agent.ask("Delete all canceled orders")
    assert not result.ok and not result.needs_clarification
    assert result.error == "Cannot answer: I can only read data."
    assert result.sql is None
    assert len(llm.calls) == 1


def test_prompt_rules_for_refusal_and_distinct_orders(sample_db):
    agent, llm = make_agent(sample_db, [sql_reply("SELECT 1 AS x"), "ok"])
    agent.ask("anything")
    prompt, system = llm.calls[0]
    assert '"action": "refuse"' in system
    assert "COUNT(DISTINCT o.order_id)" in system
    assert "Never copy an example reply" in prompt
