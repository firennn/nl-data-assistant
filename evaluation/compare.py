"""Result comparison for execution-accuracy scoring (owner: owner3)."""

from __future__ import annotations

import pandas as pd


def results_match(
    predicted: pd.DataFrame,
    gold: pd.DataFrame,
    *,
    ordered: bool = False,
    float_tolerance: float = 1e-6,
) -> bool:
    """Return True if the predicted result has the same data as the gold result.

    Column names and aliases are ignored (compare by position/values), because a correct
    query can name columns differently.

    TODO(owner3):
    - Same shape required; compare values row by row, with floats within float_tolerance.
    - If ordered is False, sort both results by all columns before comparing.
    - Decide and document how to treat extra columns (e.g. the model also returns a name
      column next to the requested count) and NULL vs NaN.
    """
    raise NotImplementedError
