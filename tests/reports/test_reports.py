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
