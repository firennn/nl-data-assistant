import pytest

from modeling.forecast import FORECAST_COLUMNS, forecast_weekly_revenue


def test_public_interface_exists():
    assert callable(forecast_weekly_revenue)
    assert FORECAST_COLUMNS == ["week", "forecast", "lower", "upper"]


@pytest.mark.skip(reason="TODO(owner2): forecast output shape and interval ordering")
def test_forecast_shape():
    """Returns horizon_weeks rows with FORECAST_COLUMNS and lower <= forecast <= upper."""
