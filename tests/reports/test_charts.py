"""Report charts on the fixture database (rows described in tests/conftest.py)."""

from datetime import date

import pandas as pd
import plotly.graph_objects as go
import pytest

from reports import charts as charts_module
from reports.charts import CHARTS, TREND_WEEKS, ReportChart, build_charts, register_chart
from reports.weekly_report import ReportChart as ReportChartFromReport
from shared.charts import render_chart
from shared.models import ChartSpec


def test_build_charts_returns_every_registered_chart(sample_db):
    result = build_charts(date(2018, 1, 7), db_path=sample_db)
    assert len(result) == len(CHARTS) == 3
    assert all(isinstance(c, ReportChart) for c in result)
    assert [c.spec.kind for c in result] == ["line", "bar", "bar"]


def test_report_chart_is_the_same_class_in_weekly_report():
    assert ReportChartFromReport is ReportChart


def test_revenue_trend_covers_twelve_weeks_oldest_first(sample_db):
    chart = CHARTS["revenue_trend"](date(2018, 1, 14), sample_db)
    data = chart.data
    assert len(data) == TREND_WEEKS
    assert data["week_ending"].iloc[0] == date(2017, 10, 29)
    assert data["week_ending"].iloc[-1] == date(2018, 1, 14)
    # Matches the Revenue metric: 405.8 in the first week of January, 0 in the second
    # (only a canceled order), and nothing before.
    assert data["revenue_brl"].iloc[-2] == pytest.approx(405.8)
    assert data["revenue_brl"].iloc[-1] == 0
    assert data["revenue_brl"].iloc[:-2].sum() == 0
    assert (chart.spec.x, chart.spec.y) == ("week_ending", "revenue_brl")


def test_top_categories_by_revenue(sample_db):
    data = CHARTS["top_categories"](date(2018, 1, 7), sample_db).data
    assert list(data["category"]) == ["sports_leisure", "bed_bath_table", "health_beauty"]
    assert list(data["revenue_brl"]) == pytest.approx([249.9, 120.0, 35.9])


def test_orders_by_state_sorted_with_ties_by_name(sample_db):
    data = CHARTS["orders_by_state"](date(2018, 1, 7), sample_db).data
    assert list(data["state"]) == ["RJ", "SP"]
    assert list(data["orders"]) == [1, 1]


def test_canceled_orders_are_left_out_of_the_bar_charts(sample_db):
    # The only order placed in the week ending 2018-01-14 is canceled.
    for name in ("top_categories", "orders_by_state"):
        assert CHARTS[name](date(2018, 1, 14), sample_db).data.empty


def test_charts_render_with_the_shared_renderer(sample_db):
    for week_end in (date(2018, 1, 7), date(2018, 1, 14)):  # with data and empty
        for chart in build_charts(week_end, db_path=sample_db):
            assert isinstance(render_chart(chart.data, chart.spec), go.Figure)


def test_new_chart_is_picked_up_from_the_registry(sample_db, monkeypatch):
    monkeypatch.setattr(charts_module, "CHARTS", {})

    @register_chart("constant")
    def constant(week_end, db_path=None):
        data = pd.DataFrame({"x": ["a"], "y": [1]})
        return ReportChart("Constant", ChartSpec(kind="bar", x="x", y="y"), data)

    assert [c.title for c in build_charts(date(2018, 1, 7), db_path=sample_db)] == ["Constant"]
    assert len(CHARTS) == 3  # the real registry is unchanged


def test_duplicate_chart_names_are_rejected():
    with pytest.raises(ValueError, match="already registered"):
        register_chart("revenue_trend")(lambda week_end, db_path=None: None)
