import pytest

from reports import export, weekly_report


def test_public_interface_exists():
    for fn in ("compute_weekly_metrics", "detect_anomalies", "build_report"):
        assert callable(getattr(weekly_report, fn))
    for fn in ("export_html", "export_pdf", "run_weekly"):
        assert callable(getattr(export, fn))


@pytest.mark.skip(reason="TODO(owner2): weekly metrics on the fixture DB")
def test_compute_weekly_metrics(sample_db):
    """For week_end=2018-01-07 the fixture has 2 non-canceled orders with revenue 405.8 BRL;
    check value, unit and previous-week comparison."""
