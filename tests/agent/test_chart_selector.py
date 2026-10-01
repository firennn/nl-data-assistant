import pandas as pd
import pytest

from agent.chart_selector import MAX_BAR_ROWS, choose_chart


@pytest.mark.parametrize(
    "df, kind, x, y",
    [
        (pd.DataFrame(), "table", None, None),
        (pd.DataFrame({"orders": [99441]}), "metric", None, "orders"),
        (pd.DataFrame({"status": ["delivered"]}), "table", None, None),
        (
            pd.DataFrame({"month": ["2018-01", "2018-02", "2018-03"], "revenue": [1.0, 2.0, 3.0]}),
            "line",
            "month",
            "revenue",
        ),
        (
            pd.DataFrame({"day": ["2018-01-01", "2018-01-02"], "orders": [5, 7]}),
            "line",
            "day",
            "orders",
        ),
        (pd.DataFrame({"year": [2017, 2018], "orders": [45101, 54011]}), "line", "year", "orders"),
        (
            pd.DataFrame({"category": ["a", "b", "c"], "revenue": [3.0, 2.0, 1.0]}),
            "bar",
            "category",
            "revenue",
        ),
        (
            pd.DataFrame({"weight_g": [100, 200, 300], "freight": [5.0, 9.0, 14.0]}),
            "scatter",
            "weight_g",
            "freight",
        ),
        (
            pd.DataFrame({"state": ["SP", "RJ"], "city": ["x", "y"], "n": [1, 2]}),
            "table",
            None,
            None,
        ),
    ],
)
def test_chart_rules(df, kind, x, y):
    spec = choose_chart(df, title="t")
    assert spec.kind == kind
    assert spec.x == x
    assert spec.y == y
    assert spec.title == "t"


def test_many_categories_fall_back_to_table():
    rows = MAX_BAR_ROWS + 1
    df = pd.DataFrame({"city": [f"c{i}" for i in range(rows)], "orders": range(rows)})
    assert choose_chart(df).kind == "table"


def test_line_with_several_series():
    df = pd.DataFrame(
        {"month": ["2018-01", "2018-01", "2018-02"], "state": ["SP", "RJ", "SP"], "n": [1, 2, 3]}
    )
    spec = choose_chart(df)
    assert (spec.kind, spec.x, spec.y, spec.color) == ("line", "month", "n", "state")


def test_numeric_column_not_named_like_time_is_not_a_time_axis():
    df = pd.DataFrame({"installments": [1, 2, 3], "orders": [10, 20, 30]})
    assert choose_chart(df).kind == "scatter"
