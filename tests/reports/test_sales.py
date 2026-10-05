"""The weekly report on uploaded sales data (reports/sales.py), built from a generated CSV
export through data.upload.build_user_db. Expected numbers are computed with pandas."""

from datetime import date, timedelta

import pandas as pd
import pytest

from data.upload import build_user_db
from reports.export import generate_report
from reports.sales import (
    MappingError,
    SalesMapping,
    check_mapping,
    describe_tables,
    suggest_mapping,
    weekly_revenue,
)
from reports.summary import format_number
from reports.weekly_report import build_report
from shared.llm import FakeProvider, LLMError

FIRST, LAST = date(2024, 1, 1), date(2024, 4, 30)
SPIKE = date(2024, 4, 26)


def make_sales() -> pd.DataFrame:
    """2-4 orders a day, two lines each, 20 customers at a time; a spike on SPIKE."""
    rows = []
    day, n = FIRST, 0
    while day <= LAST:
        orders = 30 if day == SPIKE else 2 + day.day % 3
        for k in range(orders):
            n += 1
            for line, category in enumerate(("Coffee", "Tea" if k % 2 else "Snacks")):
                rows.append(
                    {
                        "Invoice No": f"INV{n:05d}",
                        "Order Date": day.strftime("%m/%d/%Y"),
                        "Customer": f"C{(n * 7) % 20 + (n // 150) * 20:03d}",
                        "Category": category,
                        "Net Sales": round(10 + (n % 5) * 2.5 + line, 2),
                        "Quantity": 1 + line,
                    }
                )
        day += timedelta(days=1)
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def sales():
    return make_sales()


@pytest.fixture(scope="module")
def upload(tmp_path_factory, sales):
    folder = tmp_path_factory.mktemp("sales")
    path = folder / "POS Export.csv"
    sales.to_csv(path, index=False)
    return build_user_db([path], folder / "db", dayfirst=False)


@pytest.fixture(scope="module")
def mapping(upload):
    return suggest_mapping(describe_tables(upload.db_path), currency="USD")


def expected(sales: pd.DataFrame, start: date, end: date) -> dict:
    days = pd.to_datetime(sales["Order Date"], format="%m/%d/%Y").dt.date
    week = sales[(days >= start) & (days <= end)]
    first_seen = sales.assign(day=days).groupby("Customer")["day"].min()
    return {
        "Revenue": week["Net Sales"].sum(),
        "Orders": week["Invoice No"].nunique(),
        "Average order value": week["Net Sales"].sum() / week["Invoice No"].nunique(),
        "Customers": week["Customer"].nunique(),
        "New customers": int(((first_seen >= start) & (first_seen <= end)).sum()),
    }


def test_suggested_mapping_from_column_names(upload, mapping):
    assert mapping == SalesMapping(
        table="pos_export",
        date_column="order_date",
        amount_column="net_sales",
        order_column="invoice_no",
        customer_column="customer",
        category_column="category",
        currency="USD",
    )
    tables = describe_tables(upload.db_path)
    assert tables["pos_export"].date_columns == ["order_date"]
    assert "quantity" in tables["pos_export"].numeric_columns


def test_no_suggestion_without_a_date_and_a_number(tmp_path):
    path = tmp_path / "people.csv"
    path.write_text("name,city\nAna,Lyon\n", encoding="utf-8")
    up = build_user_db([path], tmp_path / "db")
    assert suggest_mapping(describe_tables(up.db_path)) is None


def test_check_mapping(upload, mapping):
    assert check_mapping(mapping, upload.db_path) == (FIRST, LAST)
    with pytest.raises(MappingError, match="no column"):
        check_mapping(SalesMapping("pos_export", "order_date", "nope"), upload.db_path)
    with pytest.raises(MappingError, match="not numbers"):
        check_mapping(SalesMapping("pos_export", "order_date", "customer"), upload.db_path)
    with pytest.raises(MappingError, match="does not contain dates"):
        check_mapping(SalesMapping("pos_export", "customer", "net_sales"), upload.db_path)
    with pytest.raises(MappingError, match="no table"):
        check_mapping(SalesMapping("nope", "a", "b"), upload.db_path)


def test_report_metrics_match_pandas(upload, mapping, sales):
    week_end = date(2024, 4, 28)
    report = build_report(week_end, db_path=upload.db_path, llm=FakeProvider([LLMError("x")]),
                          mapping=mapping)  # fmt: skip
    this_week = expected(sales, date(2024, 4, 22), week_end)
    last_week = expected(sales, date(2024, 4, 15), date(2024, 4, 21))
    got = {m.name: m for m in report.metrics}
    assert list(got) == list(this_week)
    for name, value in this_week.items():
        assert got[name].value == pytest.approx(value)
        assert got[name].comparison_value == pytest.approx(last_week[name])
    assert got["Revenue"].unit == "USD" and got["Orders"].unit == "orders"
    assert report.currency == "USD"


def test_report_flags_the_spike_and_forecasts(upload, mapping):
    report = build_report(date(2024, 4, 28), db_path=upload.db_path, llm=FakeProvider(["ok"]),
                          mapping=mapping)  # fmt: skip
    flagged = report.anomalies[report.anomalies["date"] == SPIKE]
    assert set(flagged["metric"]) == {"Revenue", "Orders"}
    assert len(report.forecast) == 4
    assert [c.spec.kind for c in report.charts] == ["line", "bar"]
    assert report.charts[1].data["category"].iloc[0] == "Coffee"
    assert "Top category by revenue: Coffee" in report.facts
    assert "BRL" not in report.facts


def test_weekly_history_uses_full_weeks_only(upload, mapping, sales):
    history = weekly_revenue(mapping, through=date(2024, 4, 28), db_path=upload.db_path)
    assert history["week"].iloc[0] == date(2024, 1, 1)  # 2024-01-01 is a Monday
    assert history["week"].iloc[-1] == date(2024, 4, 22)
    week1 = expected(sales, date(2024, 1, 1), date(2024, 1, 7))["Revenue"]
    assert history["revenue"].iloc[0] == pytest.approx(week1)
    with pytest.raises(ValueError, match="Sunday"):
        weekly_revenue(mapping, through=date(2024, 4, 27), db_path=upload.db_path)


def test_mapping_without_optional_columns(upload, sales):
    mapping = SalesMapping("pos_export", "order_date", "net_sales")
    report = build_report(date(2024, 4, 28), db_path=upload.db_path, llm=FakeProvider(["ok"]),
                          mapping=mapping)  # fmt: skip
    assert [m.name for m in report.metrics] == [
        "Revenue",
        "Transactions",
        "Average transaction value",
    ]
    rows = expected(sales, date(2024, 4, 22), date(2024, 4, 28))
    lines = make_sales()
    days = pd.to_datetime(lines["Order Date"], format="%m/%d/%Y").dt.date
    n_lines = int(((days >= date(2024, 4, 22)) & (days <= date(2024, 4, 28))).sum())
    assert report.metrics[1].value == n_lines
    assert report.metrics[0].value == pytest.approx(rows["Revenue"])
    assert len(report.charts) == 1
    assert report.currency == ""


def test_generate_report_defaults_to_the_last_date(upload, mapping, tmp_path):
    result = generate_report(out_dir=tmp_path, db_path=upload.db_path, mapping=mapping,
                             llm=FakeProvider([LLMError("x")]), dataset="POS Export.csv",
                             pdf=True)  # fmt: skip
    assert result.summary["week_end"] == LAST.isoformat()
    assert result.summary["currency"] == "USD"
    assert result.summary["dataset"] == "POS Export.csv"
    html = result.html_path.read_text(encoding="utf-8")
    assert "POS Export.csv" in html and " USD" in html and "BRL" not in html
    assert result.pdf_path.exists()


def test_format_number_units():
    assert format_number(1234.5, "BRL") == "1,234.50 BRL"
    assert format_number(1234.5, "USD") == "1,234.50 USD"
    assert format_number(1234.5, "") == "1,234.50"
    assert format_number(1234.4, "orders") == "1,234"
    assert format_number(1234.4) == "1,234"
    assert format_number(91.234, "%") == "91.2%"
    assert format_number(float("nan"), "USD") == "n/a"
