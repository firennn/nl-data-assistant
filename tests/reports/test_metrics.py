"""Weekly metrics on the fixture database (see tests/conftest.py for the rows).

Fixture summary:
- o1 (customer u1) delivered, placed 2018-01-02, items 120.0 + 35.9, delivered 01-08, due 01-15
- o2 (customer u2) delivered, placed 2018-01-05, item 249.9, delivered 01-20, due 01-18 (late)
- o3 (customer u1) canceled, placed 2018-01-09, item 35.9
- reviews: score 5 created 01-09 (o1), score 2 created 01-21 (o2)
"""

import math
from datetime import date

import pytest

from reports import metrics as metrics_module
from reports.metrics import METRICS, MetricDefinition, register_metric
from reports.weekly_report import compute_weekly_metrics, week_bounds


def by_name(week_end, db_path):
    return {m.name: m for m in compute_weekly_metrics(week_end, db_path=db_path)}


def test_week_bounds_are_seven_inclusive_days():
    assert week_bounds(date(2018, 1, 7)) == (date(2018, 1, 1), date(2018, 1, 7))


def test_all_registered_metrics_are_returned_in_order(sample_db):
    results = compute_weekly_metrics(date(2018, 1, 7), db_path=sample_db)
    assert [m.name for m in results] == list(METRICS)
    assert [m.unit for m in results] == ["BRL", "orders", "BRL", "customers", "score", "%"]


def test_first_week(sample_db):
    m = by_name(date(2018, 1, 7), sample_db)
    assert m["Revenue"].value == pytest.approx(405.8)
    assert m["Orders"].value == 2
    assert m["Average order value"].value == pytest.approx(202.9)
    assert m["New customers"].value == 2
    # No reviews or deliveries yet this week, and nothing in the week before.
    assert math.isnan(m["Average review score"].value)
    assert math.isnan(m["On-time delivery rate"].value)
    assert m["Orders"].comparison_value == 0
    assert m["Average order value"].comparison_value is None


def test_canceled_orders_are_excluded(sample_db):
    m = by_name(date(2018, 1, 14), sample_db)
    assert m["Revenue"].value == 0  # only the canceled order o3 was placed this week
    assert m["Orders"].value == 0
    assert math.isnan(m["Average order value"].value)
    # u1 already ordered in the first week, so the canceled order adds no new customer.
    assert m["New customers"].value == 0
    assert m["Revenue"].comparison_value == pytest.approx(405.8)
    assert m["Revenue"].change_pct == pytest.approx(-100.0)


def test_reviews_and_deliveries_count_in_the_week_they_happen(sample_db):
    second = by_name(date(2018, 1, 14), sample_db)
    assert second["Average review score"].value == 5
    assert second["On-time delivery rate"].value == 100  # o1 delivered 01-08, due 01-15

    third = by_name(date(2018, 1, 21), sample_db)
    assert third["Average review score"].value == 2
    assert third["On-time delivery rate"].value == 0  # o2 delivered 01-20, due 01-18
    assert third["On-time delivery rate"].comparison_value == 100
    assert third["Average review score"].change_pct == pytest.approx(-60.0)


def test_week_boundaries_are_inclusive(sample_db):
    # 2017-12-30 .. 2018-01-05 includes o2, placed on the last day.
    assert by_name(date(2018, 1, 5), sample_db)["Revenue"].value == pytest.approx(405.8)
    # 2017-12-29 .. 2018-01-04 includes only o1.
    assert by_name(date(2018, 1, 4), sample_db)["Revenue"].value == pytest.approx(155.9)


def test_new_metric_is_picked_up_from_the_registry(sample_db, monkeypatch):
    monkeypatch.setattr(metrics_module, "METRICS", dict(METRICS))
    monkeypatch.setattr("reports.weekly_report.METRICS", metrics_module.METRICS)

    @register_metric("Constant", "units")
    def constant(start, end, db_path=None):
        return 1.0

    results = by_name(date(2018, 1, 7), sample_db)
    assert results["Constant"].value == 1.0
    assert "Constant" not in METRICS  # the real registry is unchanged


def test_duplicate_metric_names_are_rejected():
    with pytest.raises(ValueError, match="already registered"):
        register_metric("Revenue", "BRL")(lambda start, end, db_path=None: 0.0)


def test_metric_definition_is_immutable():
    definition = METRICS["Revenue"]
    assert isinstance(definition, MetricDefinition)
    with pytest.raises(AttributeError):
        definition.unit = "USD"
