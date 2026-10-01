from datetime import date

import pytest

from reports import export, weekly_report


def test_public_interface_exists():
    for fn in ("compute_weekly_metrics", "detect_anomalies", "build_report"):
        assert callable(getattr(weekly_report, fn))
    for fn in ("export_html", "export_pdf", "run_weekly"):
        assert callable(getattr(export, fn))


def test_compute_weekly_metrics(sample_db):
    """For week_end=2018-01-07 the fixture has 2 non-canceled orders with revenue 405.8 BRL;
    check value, unit and previous-week comparison."""
    metrics = weekly_report.compute_weekly_metrics(date(2018, 1, 7), db_path=sample_db)
    revenue = next(m for m in metrics if m.name == "Revenue")
    assert revenue.value == pytest.approx(405.8)
    assert revenue.unit == "BRL"
    assert revenue.period_start == date(2018, 1, 1)
    assert revenue.period_end == date(2018, 1, 7)
    assert revenue.comparison_value == 0
    assert revenue.comparison_label == "previous week"
    assert revenue.change_pct is None  # no sales in the previous week


def test_build_report_on_the_fixture(sample_db):
    from shared.llm import FakeProvider

    text = "Revenue was 405.80 BRL from 2 orders, led by sports_leisure at 249.90 BRL."
    report = weekly_report.build_report(
        date(2018, 1, 7), db_path=sample_db, llm=FakeProvider([text])
    )
    assert (report.week_start, report.week_end) == (date(2018, 1, 1), date(2018, 1, 7))
    assert [m.name for m in report.metrics][:2] == ["Revenue", "Orders"]
    assert len(report.charts) == 3
    assert list(report.anomalies["date"].unique()) == [date(2018, 1, 5)]
    assert report.metric_changes is not None
    assert len(report.forecast) == 4
    assert report.summary == text
    assert report.summary_source == "llm"
    assert "Top category by revenue: sports_leisure (249.90 BRL)." in report.facts
    assert "State with the most orders: RJ (1 orders)." in report.facts


def test_build_report_without_a_working_llm_still_has_a_summary(sample_db):
    from shared.llm import FakeProvider, LLMError

    report = weekly_report.build_report(
        date(2018, 1, 7), db_path=sample_db, llm=FakeProvider([LLMError("no key")])
    )
    assert report.summary_source == "fallback"
    assert "Revenue: 405.80 BRL" in report.summary
