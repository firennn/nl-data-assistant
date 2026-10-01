"""Result comparison for execution-accuracy scoring (owner: owner3).

A prediction is scored by the data it returns, not by its SQL text, because many different
queries are correct. The rules (also listed in docs/DECISIONS.md):

- Column names and aliases are ignored. Gold columns are matched to predicted columns by
  their values, so `revenue` and `total_revenue` are the same column.
- Extra predicted columns are allowed by default (e.g. a name column next to the requested
  count), as long as every gold column is found and the row count is the same.
- Row order only matters when `ordered=True` (rankings, top-N); otherwise rows are compared
  as an unordered collection.
- Numbers match within an absolute tolerance (default 0.005, so a value rounded to 2 decimals
  matches the unrounded one) or a relative tolerance (default 0.01%). 5 equals 5.0, and a text
  value that is a plain number (e.g. a year returned by strftime) is compared as a number.
- NULL, None and NaN are all treated as the same missing value.
- Text is compared exactly after trimming surrounding spaces (case-sensitive).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import pandas as pd

# A plain number without leading zeros, so ids and zip codes such as "01001" stay text.
_NUMBER_TEXT = re.compile(r"^-?(0|[1-9]\d*)(\.\d+)?$")


@dataclass
class Comparison:
    """Outcome of comparing a predicted result with the gold result."""

    match: bool
    reason: str = ""  # why it did not match, empty on a match
    extra_columns: int = 0  # predicted columns not needed to match the gold result


def _normalize(value: object) -> object:
    """Map a cell to None, float or str so values from different queries compare cleanly."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):  # lists, arrays and other non-scalars
        return str(value)
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        return float(value)
    if hasattr(value, "item"):  # numpy scalars
        return _normalize(value.item())
    text = str(value).strip()
    if _NUMBER_TEXT.match(text):
        return float(text)
    return text


def _sort_key(value: object) -> tuple[int, object]:
    """Order missing values, numbers and text consistently (they cannot be compared directly)."""
    if value is None:
        return (0, 0)
    if isinstance(value, float):
        return (1, value)
    return (2, value)


def _values_equal(a: object, b: object, abs_tol: float, rel_tol: float) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return math.isclose(a, b, rel_tol=rel_tol, abs_tol=abs_tol)
    return a == b


def _sequences_equal(a: list, b: list, abs_tol: float, rel_tol: float) -> bool:
    return len(a) == len(b) and all(
        _values_equal(x, y, abs_tol, rel_tol) for x, y in zip(a, b, strict=True)
    )


def _rows_equal(a: list[tuple], b: list[tuple], abs_tol: float, rel_tol: float) -> bool:
    return len(a) == len(b) and all(
        _sequences_equal(list(x), list(y), abs_tol, rel_tol) for x, y in zip(a, b, strict=True)
    )


def _row_key(row: tuple) -> tuple:
    return tuple(_sort_key(v) for v in row)


def compare_results(
    predicted: pd.DataFrame | None,
    gold: pd.DataFrame,
    *,
    ordered: bool = False,
    float_tolerance: float = 0.005,
    rel_tolerance: float = 1e-4,
    allow_extra_columns: bool = True,
) -> Comparison:
    """Compare a predicted result with the gold result and explain any mismatch."""
    if predicted is None:
        return Comparison(False, "no result")

    n_pred, n_gold = predicted.shape[1], gold.shape[1]
    if n_pred < n_gold:
        return Comparison(False, f"too few columns ({n_pred} instead of {n_gold})")
    if n_pred > n_gold and not allow_extra_columns:
        return Comparison(False, f"too many columns ({n_pred} instead of {n_gold})")
    if len(predicted) != len(gold):
        return Comparison(False, f"wrong row count ({len(predicted)} instead of {len(gold)})")

    pred_cols = [[_normalize(v) for v in predicted.iloc[:, i]] for i in range(n_pred)]
    gold_cols = [[_normalize(v) for v in gold.iloc[:, j]] for j in range(n_gold)]
    tolerances = (float_tolerance, rel_tolerance)

    if _columns_match(pred_cols, gold_cols, ordered, *tolerances):
        return Comparison(True, extra_columns=n_pred - n_gold)
    if ordered and _columns_match(pred_cols, gold_cols, False, *tolerances):
        return Comparison(False, "row order differs")
    return Comparison(False, "values differ")


def _columns_match(
    pred_cols: list[list],
    gold_cols: list[list],
    ordered: bool,
    abs_tol: float,
    rel_tol: float,
) -> bool:
    """Whether some choice of distinct predicted columns reproduces the gold rows."""
    if not gold_cols or not gold_cols[0]:
        return True  # no columns or no rows: shape checks already passed

    def column_fits(p: list, g: list) -> bool:
        if not ordered:
            p, g = sorted(p, key=_sort_key), sorted(g, key=_sort_key)
        return _sequences_equal(p, g, abs_tol, rel_tol)

    # Predicted columns that could hold each gold column, judged column by column.
    candidates = [[i for i, p in enumerate(pred_cols) if column_fits(p, g)] for g in gold_cols]
    if any(not c for c in candidates):
        return False

    gold_rows = list(zip(*gold_cols, strict=True))
    if not ordered:
        gold_rows.sort(key=_row_key)

    def rows_fit(mapping: list[int]) -> bool:
        rows = list(zip(*(pred_cols[i] for i in mapping), strict=True))
        if not ordered:
            rows.sort(key=_row_key)
        return _rows_equal(rows, gold_rows, abs_tol, rel_tol)

    # Try each way of assigning distinct predicted columns to the gold columns (results have
    # few columns, so this search is small).
    def search(j: int, used: list[int]) -> bool:
        if j == len(gold_cols):
            return rows_fit(used)
        return any(search(j + 1, [*used, i]) for i in candidates[j] if i not in used)

    return search(0, [])


def results_match(
    predicted: pd.DataFrame | None,
    gold: pd.DataFrame,
    *,
    ordered: bool = False,
    float_tolerance: float = 0.005,
    rel_tolerance: float = 1e-4,
    allow_extra_columns: bool = True,
) -> bool:
    """Return True if the predicted result has the same data as the gold result.

    See the module docstring for the rules; `compare_results` also returns the reason for a
    mismatch and the number of extra columns.
    """
    return compare_results(
        predicted,
        gold,
        ordered=ordered,
        float_tolerance=float_tolerance,
        rel_tolerance=rel_tolerance,
        allow_extra_columns=allow_extra_columns,
    ).match
