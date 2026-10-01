import json

from agent import cli
from agent.sql_agent import SQLAgent
from shared.config import Settings
from shared.llm import FakeProvider


def make_agent(sample_db, replies):
    return SQLAgent(llm=FakeProvider(replies), settings=Settings(db_path=sample_db))


def sql_reply(sql):
    return json.dumps({"action": "sql", "sql": sql, "assumptions": ""})


def test_cli_prints_explanation_sql_and_rows(sample_db, capsys):
    agent = make_agent(
        sample_db,
        [sql_reply("SELECT status, COUNT(*) AS n FROM orders GROUP BY status"), "Two statuses."],
    )
    code = cli.main(["How", "many", "orders", "per", "status?"], agent=agent)
    out = capsys.readouterr().out
    assert code == 0
    assert "Two statuses." in out
    assert "SQL used:" in out and "GROUP BY status" in out
    assert "delivered" in out and "Chart: bar" in out


def test_cli_clarification_exit_code(sample_db, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    agent = make_agent(sample_db, [json.dumps({"action": "clarify", "question": "Which year?"})])
    code = cli.main(["Show revenue"], agent=agent)
    assert code == 2
    assert "Question for you: Which year?" in capsys.readouterr().out


def test_cli_error_exit_code(sample_db, capsys):
    agent = make_agent(sample_db, [sql_reply("DROP TABLE orders")])
    code = cli.main(["Delete everything"], agent=agent)
    assert code == 1
    assert "Blocked unsafe query" in capsys.readouterr().out


def test_cli_saves_chart(sample_db, tmp_path, capsys):
    agent = make_agent(sample_db, [sql_reply("SELECT COUNT(*) AS n FROM orders"), "ok"])
    out_file = tmp_path / "chart.html"
    assert cli.main(["How many orders?", "--chart", str(out_file)], agent=agent) == 0
    assert out_file.exists() and "plotly" in out_file.read_text(encoding="utf-8").lower()


def test_interactive_mode_keeps_history(sample_db, monkeypatch, capsys):
    llm = FakeProvider(
        [
            json.dumps({"action": "clarify", "question": "By revenue or by score?"}),
            sql_reply("SELECT 1 AS x"),
            "ok",
        ]
    )
    agent = SQLAgent(llm=llm, settings=Settings(db_path=sample_db))
    answers = iter(["Best sellers?", "By revenue", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert cli.main([], agent=agent) == 0
    second_prompt, _ = llm.calls[1]
    assert "User: Best sellers?" in second_prompt
    assert "Assistant: By revenue or by score?" in second_prompt
