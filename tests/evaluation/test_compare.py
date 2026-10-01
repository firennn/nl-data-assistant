import numpy as np
import pandas as pd
import pytest

from evaluation.compare import compare_results, results_match


def df(**columns):
    return pd.DataFrame(columns)


GOLD_RANKING = df(category=["toys", "books", "garden"], items=[30, 20, 10])


def test_identical_results_match():
    assert results_match(GOLD_RANKING.copy(), GOLD_RANKING, ordered=True)


def test_column_names_are_ignored():
    assert results_match(df(cat=["toys", "books", "garden"], n=[30, 20, 10]), GOLD_RANKING)


def test_column_order_is_ignored():
    assert results_match(df(n=[30, 20, 10], cat=["toys", "books", "garden"]), GOLD_RANKING)


def test_row_order_ignored_when_not_ordered():
    shuffled = df(category=["garden", "toys", "books"], items=[10, 30, 20])
    assert results_match(shuffled, GOLD_RANKING, ordered=False)


def test_row_order_matters_when_ordered():
    shuffled = df(category=["garden", "toys", "books"], items=[10, 30, 20])
    result = compare_results(shuffled, GOLD_RANKING, ordered=True)
    assert not result.match
    assert result.reason == "row order differs"


def test_rows_must_stay_together():
    # Each column holds the right values, but the pairs are mixed up.
    mixed = df(category=["toys", "books", "garden"], items=[10, 20, 30])
    assert not results_match(mixed, GOLD_RANKING)


def test_extra_columns_allowed_by_default():
    gold = df(orders=[42])
    predicted = df(category=["toys"], orders=[42])
    result = compare_results(predicted, gold)
    assert result.match
    assert result.extra_columns == 1


def test_extra_columns_rejected_when_strict():
    result = compare_results(
        df(name=["x"], orders=[42]), df(orders=[42]), allow_extra_columns=False
    )
    assert not result.match
    assert "too many columns" in result.reason


def test_missing_column_fails():
    result = compare_results(df(category=["toys", "books", "garden"]), GOLD_RANKING)
    assert not result.match
    assert "too few columns" in result.reason


def test_wrong_row_count_fails():
    result = compare_results(GOLD_RANKING.head(2), GOLD_RANKING)
    assert not result.match
    assert "wrong row count" in result.reason


def test_wrong_value_fails():
    result = compare_results(
        df(category=["toys", "books", "garden"], items=[30, 20, 11]), GOLD_RANKING
    )
    assert not result.match
    assert result.reason == "values differ"


def test_int_and_float_match():
    assert results_match(df(n=[5.0]), df(n=[5]))


@pytest.mark.parametrize(("predicted", "expected"), [(1234.57, True), (1235.0, False)])
def test_rounded_money_within_tolerance(predicted, expected):
    assert results_match(df(revenue=[predicted]), df(revenue=[1234.5678])) is expected


def test_relative_tolerance_for_large_numbers():
    assert results_match(df(revenue=[13_591_643.70]), df(revenue=[13_591_644.0]))


def test_missing_values_match_each_other():
    gold = df(city=["a", None], score=[1.0, np.nan])
    predicted = df(city=["a", np.nan], score=[1.0, None])
    assert results_match(predicted, gold, ordered=True)


def test_missing_value_does_not_match_zero():
    assert not results_match(df(n=[0]), df(n=[None]))


def test_number_text_matches_number():
    # strftime returns text, so a year can come back as "2017" or 2017.
    assert results_match(df(year=["2017", "2018"]), df(year=[2017, 2018]))


def test_codes_with_leading_zeros_stay_text():
    assert not results_match(df(zip=["01001"]), df(zip=[1001]))


def test_text_is_trimmed_but_case_sensitive():
    assert results_match(df(state=[" SP "]), df(state=["SP"]))
    assert not results_match(df(state=["sp"]), df(state=["SP"]))


def test_empty_results_match():
    assert results_match(df(n=[]), df(orders=[]))


def test_no_prediction_fails():
    result = compare_results(None, GOLD_RANKING)
    assert not result.match
    assert result.reason == "no result"


def test_duplicate_values_choose_distinct_columns():
    # Two gold columns with the same values must map to two different predicted columns.
    gold = df(a=[1, 2], b=[1, 2])
    assert not results_match(df(x=[1, 2], y=["p", "q"]), gold)
    assert results_match(df(x=[1, 2], y=[1, 2]), gold)
