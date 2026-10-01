"""HTML export, JSON summary, generate_report and the command line, on the fixture database."""

import json
from datetime import date

import pytest

from reports import export
from reports.export import (
    DEFAULT_WEEK_END,
    export_html,
    export_pdf,
    generate_report,
    report_to_dict,
    run_weekly,
)
from reports.weekly_report import build_report
from shared.llm import FakeProvider, LLMError

SUMMARY = "Revenue was 405.80 BRL from 2 orders <b>this week</b>."


@pytest.fixture
def report(sample_db):
    return build_report(date(2018, 1, 7), db_path=sample_db, llm=FakeProvider([SUMMARY]))


def test_export_html_writes_every_section(report, tmp_path):
    path = export_html(report, tmp_path / "report.html")
    html = path.read_text(encoding="utf-8")
    assert path == tmp_path / "report.html"
    for heading in (
        "Summary",
        "Key metrics",
        "Charts",
        "Week-over-week changes",
        "Anomalies",
        "Revenue forecast",
    ):
        assert f"<h2>{heading}</h2>" in html
    assert "Weekly report: 2018-01-01 to 2018-01-07" in html
    assert "<td>Revenue</td><td>405.80 BRL</td><td>0.00 BRL</td><td>n/a</td>" in html
    assert html.count('class="plotly-graph-div"') == 3
    assert html.count("cdn.plot.ly") == 1  # Plotly is loaded once


def test_summary_text_is_escaped(report, tmp_path):
    html = export_html(report, tmp_path / "report.html").read_text(encoding="utf-8")
    assert "&lt;b&gt;this week&lt;/b&gt;" in html
    assert "<b>this week</b>" not in html


def test_fallback_summary_is_labeled(sample_db, tmp_path):
    fallback = build_report(
        date(2018, 1, 7), db_path=sample_db, llm=FakeProvider([LLMError("no key")])
    )
    html = export_html(fallback, tmp_path / "report.html").read_text(encoding="utf-8")
    assert "lists the computed facts" in html


def test_default_path_uses_the_week_end(report, tmp_path, monkeypatch):
    monkeypatch.setattr(export, "OUTPUT_DIR", tmp_path)
    assert export_html(report) == tmp_path / "weekly_report_2018-01-07.html"


def test_report_to_dict_is_json_ready(report):
    data = report_to_dict(report)
    json.dumps(data)  # no NaN, dates or numpy values left
    assert data["week_end"] == "2018-01-07"
    assert data["summary_source"] == "llm"
    revenue = data["metrics"][0]
    assert revenue == {
        "name": "Revenue",
        "value": pytest.approx(405.8),
        "previous": 0.0,
        "change_pct": None,
        "unit": "BRL",
    }
    review = next(m for m in data["metrics"] if m["name"] == "Average review score")
    assert review["value"] is None  # NaN becomes null
    assert {a["date"] for a in data["anomalies"]} == {"2018-01-05"}
    assert len(data["forecast"]["weeks"]) == 4


def test_generate_report_writes_html_and_json(sample_db, tmp_path):
    result = generate_report(
        "2018-01-07", out_dir=tmp_path, db_path=sample_db, llm=FakeProvider([SUMMARY])
    )
    assert result.html_path == tmp_path / "weekly_report_2018-01-07.html"
    assert result.json_path == tmp_path / "weekly_report_2018-01-07.json"
    assert json.loads(result.json_path.read_text(encoding="utf-8")) == result.summary


def test_run_weekly_returns_the_html_path(sample_db, tmp_path):
    path = run_weekly(
        "2018-01-07", out_dir=tmp_path, db_path=sample_db, llm=FakeProvider([SUMMARY])
    )
    assert path.name == "weekly_report_2018-01-07.html"
    assert path.exists()


def test_week_end_defaults_to_the_last_full_week_and_is_validated(monkeypatch):
    seen = []
    monkeypatch.setattr(export, "build_report", lambda week, **kw: seen.append(week) or 1 / 0)
    with pytest.raises(ZeroDivisionError):
        generate_report()
    assert seen == [DEFAULT_WEEK_END] == [date(2018, 8, 19)]
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        generate_report("19-08-2018")


def test_command_line(sample_db, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(export, "build_report", _fixture_report(sample_db))
    export.main(["--week-end", "2018-01-07", "--out", str(tmp_path), "--db", str(sample_db)])
    output = capsys.readouterr().out
    assert "HTML:" in output and "JSON:" in output
    assert (tmp_path / "weekly_report_2018-01-07.html").exists()


def test_command_line_rejects_a_bad_date(capsys):
    with pytest.raises(SystemExit):
        export.main(["--week-end", "next monday"])
    assert "YYYY-MM-DD" in capsys.readouterr().err


def test_pdf_export_is_not_available_yet(report):
    with pytest.raises(NotImplementedError, match="print it to PDF"):
        export_pdf(report)


def _fixture_report(sample_db):
    def build(week, **kwargs):
        kwargs["llm"] = FakeProvider([SUMMARY])
        return build_report(week, **kwargs)

    return build
