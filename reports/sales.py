"""Weekly report on any sales table, for example an uploaded CSV export.

The Olist report reads fixed tables (reports/metrics.py, charts.py, anomalies.py). For other
data the user says which columns hold what in a SalesMapping: one table with a date and an
amount per row, and optionally an order id, a customer and a category. This module turns the
mapping into the same building blocks as the Olist report (metric definitions, daily series,
charts and weekly revenue history), so anomalies, the forecast, the summary and the export are
shared.

Definitions for mapped data:
- Revenue is the sum of the amount column for rows dated in the period. Every row counts, so
  refunds or cancellations should be removed (or be negative) in the file itself.
- Orders are distinct order ids; without an order column every row is one transaction.
- A customer is new in the period of the first date they appear anywhere in the table.
- Rows whose date cannot be read are left out.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from reports.charts import TOP_N, TREND_WEEKS, ReportChart
from reports.metrics import MetricDefinition
from shared.db import run_query
from shared.models import ChartSpec
from shared.schema import describe_schema

DbPath = str | Path | None

DATE_PROBE_ROWS = 200  # values checked to decide whether a column holds dates
NUMERIC_TYPES = ("INT", "REAL", "FLOA", "DOUB", "NUM", "DEC")

# Name hints for suggest_mapping, strongest first. Matched against snake_case column names.
_AMOUNT_HINTS = ("revenue", "sales", "amount", "total", "net", "gross", "price", "value", "paid")
_DATE_HINTS = ("order_date", "date", "time", "day", "created", "purchase")
_ORDER_HINTS = ("order", "invoice", "transaction", "receipt", "ticket", "bill")
_CUSTOMER_HINTS = ("customer", "client", "buyer", "member", "account", "user")
_CATEGORY_HINTS = ("category", "department", "segment", "product_type", "type", "product", "item")
_ID_LIKE = re.compile(r"(^|_)(id|no|num|number|code|zip|postal|phone|qty|quantity|count)($|_)")


class MappingError(ValueError):
    """The mapping cannot be used. The message is written to be shown to the user."""


@dataclass(frozen=True)
class SalesMapping:
    table: str
    date_column: str
    amount_column: str
    order_column: str | None = None
    customer_column: str | None = None
    category_column: str | None = None
    currency: str = ""  # shown after money values, e.g. "USD"; empty for none

    @property
    def unit(self) -> str:
        return self.currency.strip()

    @property
    def orders_name(self) -> str:
        return "Orders" if self.order_column else "Transactions"


@dataclass
class TableColumns:
    """Columns of one table, grouped by what they can be used for."""

    name: str
    rows: int
    columns: list[str] = field(default_factory=list)
    date_columns: list[str] = field(default_factory=list)
    numeric_columns: list[str] = field(default_factory=list)


# --- inspecting the database -----------------------------------------------------------------


def describe_tables(db_path: DbPath) -> dict[str, TableColumns]:
    """Every table with its columns, date columns (every probed value is a date written as
    text) and numeric columns."""
    schema = describe_schema(db_path, sample_values=0)
    result = {}
    for table in schema.tables:
        names = [c.name for c in table.columns]
        numeric = [c.name for c in table.columns if c.type.upper().startswith(NUMERIC_TYPES)]
        dates = [n for n in names if n not in numeric and _is_date_column(table.name, n, db_path)]
        result[table.name] = TableColumns(table.name, table.row_count, names, dates, numeric)
    return result


def _is_date_column(table: str, column: str, db_path: DbPath) -> bool:
    sql = f"""
        SELECT COUNT(*) AS n,
               SUM(CASE WHEN typeof(v) = 'text' AND date(v) IS NOT NULL THEN 1 ELSE 0 END) AS ok
        FROM (SELECT {_q(column)} AS v FROM {_q(table)} WHERE {_q(column)} IS NOT NULL LIMIT ?)
    """
    row = run_query(sql, [DATE_PROBE_ROWS], db_path=db_path).iloc[0]
    return int(row["n"]) > 0 and int(row["ok"] or 0) == int(row["n"])


def suggest_mapping(tables: dict[str, TableColumns], currency: str = "") -> SalesMapping | None:
    """A first guess at the mapping from column names, or None if no table has both a date
    column and a numeric column. The largest suitable table is used."""
    usable = [t for t in tables.values() if t.date_columns and _amount_candidates(t)]
    if not usable:
        return None
    table = max(usable, key=lambda t: t.rows)
    others = [c for c in table.columns if c not in table.date_columns]
    return SalesMapping(
        table=table.name,
        date_column=_best(table.date_columns, _DATE_HINTS) or table.date_columns[0],
        amount_column=_best(_amount_candidates(table), _AMOUNT_HINTS)
        or _amount_candidates(table)[0],
        order_column=_best(others, _ORDER_HINTS, id_like=True),
        customer_column=_best(others, _CUSTOMER_HINTS),
        category_column=_best(
            [c for c in others if c not in table.numeric_columns], _CATEGORY_HINTS, ids=False
        ),
        currency=currency,
    )


def _amount_candidates(table: TableColumns) -> list[str]:
    return [c for c in table.numeric_columns if not _ID_LIKE.search(c)] or table.numeric_columns


def _best(
    columns: list[str], hints: tuple[str, ...], *, id_like: bool = False, ids: bool = True
) -> str | None:
    """The column matching the earliest hint; with id_like, id-style names win ties."""
    for hint in hints:
        found = [c for c in columns if hint in c and (ids or not _ID_LIKE.search(c))]
        if id_like:
            found.sort(key=lambda c: not _ID_LIKE.search(c))
        if found:
            return found[0]
    return None


def check_mapping(mapping: SalesMapping, db_path: DbPath) -> tuple[date, date]:
    """Check that the mapping fits the database and return the first and last date in the
    data. Raises MappingError with a message for the user."""
    tables = describe_schema(db_path, sample_values=0)
    table = next((t for t in tables.tables if t.name == mapping.table), None)
    if table is None:
        raise MappingError(f"There is no table called {mapping.table}.")
    names = {c.name for c in table.columns}
    used = [
        mapping.date_column,
        mapping.amount_column,
        mapping.order_column,
        mapping.customer_column,
        mapping.category_column,
    ]
    missing = [c for c in used if c and c not in names]
    if missing:
        raise MappingError(f"Table {mapping.table} has no column {', '.join(missing)}.")
    if mapping.date_column == mapping.amount_column:
        raise MappingError("Choose different columns for the date and the amount.")
    t, d, a = _q(mapping.table), _q(mapping.date_column), _q(mapping.amount_column)
    not_numbers = f"{a} IS NOT NULL AND typeof({a}) NOT IN ('integer', 'real')"
    bad = run_query(f"SELECT COUNT(*) FROM {t} WHERE {not_numbers}", db_path=db_path)
    bad_amounts = int(bad.iat[0, 0])
    if bad_amounts:
        raise MappingError(
            f"Column {mapping.amount_column} has {bad_amounts:,} values that are not numbers, "
            "so it cannot be used as the amount."
        )
    first, last = run_query(
        f"SELECT MIN(date({d})), MAX(date({d})) FROM {t} WHERE typeof({d}) = 'text'",
        db_path=db_path,
    ).iloc[0]
    if first is None:
        raise MappingError(f"Column {mapping.date_column} does not contain dates.")
    return date.fromisoformat(first), date.fromisoformat(last)


# --- metrics ---------------------------------------------------------------------------------


def metric_definitions(mapping: SalesMapping) -> list[MetricDefinition]:
    """The report metrics for this mapping, in display order."""
    t, d, a = _q(mapping.table), _q(mapping.date_column), _q(mapping.amount_column)
    period = f"FROM {t} WHERE date({d}) BETWEEN ? AND ?"
    count = f"COUNT(DISTINCT {_q(mapping.order_column)})" if mapping.order_column else "COUNT(*)"
    unit = mapping.unit
    metrics = [
        MetricDefinition("Revenue", unit, _scalar_fn(f"SELECT COALESCE(SUM({a}), 0) {period}")),
        MetricDefinition(
            mapping.orders_name,
            mapping.orders_name.lower(),
            _scalar_fn(f"SELECT {count} {period}"),
        ),
        MetricDefinition(
            f"Average {mapping.orders_name.lower().removesuffix('s')} value",
            unit,
            _scalar_fn(f"SELECT SUM({a}) * 1.0 / NULLIF({count}, 0) {period}"),
        ),
    ]
    if mapping.customer_column:
        c = _q(mapping.customer_column)
        metrics.append(
            MetricDefinition(
                "Customers",
                "customers",
                _scalar_fn(f"SELECT COUNT(DISTINCT {c}) {period}"),
            )
        )
        first_seen = f"""
            WITH first_seen AS (
                SELECT {c} AS customer, MIN(date({d})) AS first_date
                FROM {t}
                WHERE {c} IS NOT NULL AND date({d}) IS NOT NULL
                GROUP BY {c}
            )
            SELECT COUNT(*) FROM first_seen WHERE first_date BETWEEN ? AND ?
        """
        metrics.append(MetricDefinition("New customers", "customers", _scalar_fn(first_seen)))
    return metrics


def _scalar_fn(sql: str):
    def compute(start: date, end: date, db_path: DbPath = None) -> float:
        df = run_query(sql, [start.isoformat(), end.isoformat()], db_path=db_path)
        value = df.iat[0, 0] if not df.empty else None
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return math.nan
        return float(value)

    return compute


# --- daily series for anomaly detection ------------------------------------------------------


def daily_series(mapping: SalesMapping) -> dict:
    """Loaders (start, end, db_path) -> one value per day, for reports.anomalies."""
    t, d, a = _q(mapping.table), _q(mapping.date_column), _q(mapping.amount_column)
    count = f"COUNT(DISTINCT {_q(mapping.order_column)})" if mapping.order_column else "COUNT(*)"

    def loader(value_sql: str):
        sql = f"""
            SELECT date({d}) AS day, {value_sql} AS value
            FROM {t}
            WHERE date({d}) BETWEEN ? AND ?
            GROUP BY day
            ORDER BY day
        """

        def load(start: date, end: date, db_path: DbPath = None) -> pd.Series:
            df = run_query(sql, [start.isoformat(), end.isoformat()], db_path=db_path)
            series = pd.Series(df["value"].to_numpy(dtype=float), index=pd.to_datetime(df["day"]))
            return series.reindex(pd.date_range(start, end, freq="D"), fill_value=0.0)

        return load

    return {"Revenue": loader(f"SUM({a})"), mapping.orders_name: loader(count)}


# --- charts ----------------------------------------------------------------------------------


def build_charts(mapping: SalesMapping, week_end: date, db_path: DbPath) -> list[ReportChart]:
    """Weekly revenue trend, plus top categories when a category column is mapped."""
    revenue = metric_definitions(mapping)[0].compute
    week_ends = [week_end - timedelta(weeks=n) for n in range(TREND_WEEKS - 1, -1, -1)]
    trend = pd.DataFrame(
        {
            "week_ending": week_ends,
            "revenue": [revenue(end - timedelta(days=6), end, db_path) for end in week_ends],
        }
    )
    title = f"Weekly revenue, last {TREND_WEEKS} weeks"
    charts = [
        ReportChart(title, ChartSpec(kind="line", title=title, x="week_ending", y="revenue"), trend)
    ]
    if mapping.category_column:
        t, d, a = _q(mapping.table), _q(mapping.date_column), _q(mapping.amount_column)
        sql = f"""
            SELECT COALESCE(CAST({_q(mapping.category_column)} AS TEXT), 'unknown') AS category,
                   SUM({a}) AS revenue
            FROM {t}
            WHERE date({d}) BETWEEN ? AND ?
            GROUP BY category
            ORDER BY revenue DESC, category
            LIMIT ?
        """
        start = week_end - timedelta(days=6)
        data = run_query(sql, [start.isoformat(), week_end.isoformat(), TOP_N], db_path=db_path)
        title = f"Top {TOP_N} {mapping.category_column.replace('_', ' ')} by revenue"
        spec = ChartSpec(kind="bar", title=title, x="category", y="revenue")
        charts.append(ReportChart(title, spec, data))
    return charts


# --- weekly history for the forecast ---------------------------------------------------------


def weekly_revenue(mapping: SalesMapping, *, through: date, db_path: DbPath) -> pd.DataFrame:
    """Revenue for Monday-Sunday weeks from the first full week in the data to the week ending
    `through` (a Sunday). Weeks without sales are 0. Same columns as
    modeling.train.load_weekly_revenue, so modeling.forecast can use it."""
    if through.weekday() != 6:
        raise ValueError(f"through must be a Sunday, got {through} ({through:%A}).")
    first, _ = check_mapping(mapping, db_path)
    first_week = first + timedelta(days=(7 - first.weekday()) % 7)  # first Monday on/after
    t, d, a = _q(mapping.table), _q(mapping.date_column), _q(mapping.amount_column)
    sql = f"""
        SELECT date({d}, 'weekday 0', '-6 days') AS week, SUM({a}) AS revenue
        FROM {t}
        WHERE date({d}) BETWEEN ? AND ?
        GROUP BY week
    """
    df = run_query(sql, [first_week.isoformat(), through.isoformat()], db_path=db_path)
    weeks = pd.date_range(first_week, through - timedelta(days=6), freq="7D").date
    revenue = dict(zip(pd.to_datetime(df["week"]).dt.date, df["revenue"], strict=True))
    return pd.DataFrame({"week": weeks, "revenue": [float(revenue.get(w, 0.0)) for w in weeks]})


def _q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'
