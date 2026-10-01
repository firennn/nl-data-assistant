"""Rule-based chart selection for query results.

Rules are deterministic, so the same result always gets the same chart and the choice is easy
to test and explain. In order:
1. empty result or no numeric column  -> table
2. one row, one numeric value         -> metric
3. a date/period column + a number    -> line (x = period)
4. one category column + a number, at most MAX_BAR_ROWS rows -> bar
5. exactly two numeric columns        -> scatter
6. anything else                      -> table
"""

from __future__ import annotations

import re

import pandas as pd

from shared.models import ChartSpec

MAX_BAR_ROWS = 20
_TIME_NAME_RE = re.compile(r"(date|day|week|month|year|quarter|period|_ts$|time)", re.IGNORECASE)
_TIME_VALUE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?( \d{2}:\d{2}(:\d{2})?)?$|^\d{4}-W\d{2}$")


def _is_numeric(series: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series)


def _is_time(series: pd.Series) -> bool:
    """A column counts as a time axis if its values look like years, months or dates."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return True
    values = series.dropna().astype(str).head(50)
    if values.empty:
        return False
    looks_like_time = values.map(lambda v: bool(_TIME_VALUE_RE.match(v))).all()
    if _is_numeric(series):
        # Plain numbers are a time axis only when named like one (e.g. "year": 2017, 2018).
        return bool(_TIME_NAME_RE.search(str(series.name))) and looks_like_time
    return looks_like_time


def choose_chart(df: pd.DataFrame, title: str = "") -> ChartSpec:
    """Pick a chart type and axes for a query result."""
    if df.empty:
        return ChartSpec("table", title)

    columns = list(df.columns)
    time_cols = [c for c in columns if _is_time(df[c])]
    numeric_cols = [c for c in columns if _is_numeric(df[c]) and c not in time_cols]
    other_cols = [c for c in columns if c not in numeric_cols and c not in time_cols]

    if not numeric_cols:
        return ChartSpec("table", title)

    if len(df) == 1 and len(columns) == 1:
        return ChartSpec("metric", title, y=numeric_cols[0])

    if time_cols and len(df) > 1:
        y = numeric_cols[0] if len(numeric_cols) == 1 else numeric_cols
        color = other_cols[0] if len(other_cols) == 1 else None
        return ChartSpec("line", title, x=time_cols[0], y=y, color=color)

    if len(other_cols) == 1 and len(numeric_cols) == 1 and len(df) <= MAX_BAR_ROWS:
        return ChartSpec("bar", title, x=other_cols[0], y=numeric_cols[0])

    if not other_cols and len(numeric_cols) == 2 and len(df) > 1:
        return ChartSpec("scatter", title, x=numeric_cols[0], y=numeric_cols[1])

    return ChartSpec("table", title)
