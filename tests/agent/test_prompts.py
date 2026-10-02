"""Prompt builders: generic rules plus the dataset profile."""

import pytest

from agent.prompts import (
    EXAMPLES,
    EXPLAIN_SYSTEM,
    SQL_SYSTEM,
    build_explain_system,
    build_sql_prompt,
    build_sql_system,
)
from shared.models import DatasetProfile
from shared.profiles import OLIST_PROFILE

OLIST_TERMS = ["Olist", "BRL", "R$", "2018", "customer_unique_id", "order_items", "unavailable"]


def test_profile_defaults_are_safe_and_independent():
    a, b = DatasetProfile(name="a"), DatasetProfile(name="b")
    a.rules.append("x")
    assert b.rules == []  # default_factory: no shared list between instances
    assert a.include_samples is False
    assert a.currency is None and a.date_range is None and a.filter_example is None


@pytest.mark.parametrize("text", [SQL_SYSTEM, EXPLAIN_SYSTEM, EXAMPLES])
def test_generic_prompts_contain_nothing_olist_specific(text):
    for term in OLIST_TERMS:
        assert term not in text, term


def test_generic_prompts_use_the_latest_date_in_the_data():
    assert "relative to the latest date" in SQL_SYSTEM
    assert "MAX on the date column" in SQL_SYSTEM
    assert "Money is in" not in SQL_SYSTEM and "Money is in" not in EXPLAIN_SYSTEM
    assert '"excluding canceled orders", not "completed orders"' in EXPLAIN_SYSTEM


def test_olist_sql_system_keeps_the_business_rules():
    system = build_sql_system(OLIST_PROFILE)
    assert "for an e-commerce marketplace (Olist, Brazil)" in system
    assert "Revenue = SUM(order_items.price)" in system
    assert "COUNT(DISTINCT customers.customer_unique_id)" in system
    assert "COUNT(DISTINCT o.order_id)" in system
    assert "Money is in Brazilian reais (BRL). Round money" in system
    assert "complete from 2017-01-01 to about 2018-08-21" in system
    assert "relative to 2018-08, not today's date" in system
    assert "relative to the latest date" not in system


def test_olist_explain_system_keeps_currency_date_and_filter_wording():
    system = build_explain_system(OLIST_PROFILE)
    assert "Money is in Brazilian reais (BRL): write amounts like R$ 1,234.56" in system
    assert "never with a plain $ sign" in system
    assert "the data ends in Aug 2018" in system
    assert "excluding canceled and unavailable orders" in system


def test_olist_examples_are_used_in_the_sql_prompt():
    prompt = build_sql_prompt("q", "SCHEMA", examples=OLIST_PROFILE.examples)
    assert "order_items oi" in prompt and 'made-up table "sales"' not in prompt
    assert "Never copy an example reply" in prompt


def test_generic_examples_are_the_default():
    prompt = build_sql_prompt("q", "SCHEMA")
    assert 'made-up table "sales"' in prompt and "order_items" not in prompt


def test_custom_profile_appears_in_the_prompts():
    profile = DatasetProfile(
        name="Shop",
        description="a small online shop",
        rules=["Profit = revenue - cost."],
        currency="euros (EUR)",
        currency_format="EUR 1,234.56",
        date_range=("2023-01-01", "2023-12-31"),
        filter_example='"excluding refunds", not "valid sales"',
    )
    sql_system = build_sql_system(profile)
    assert "for a small online shop." in sql_system
    assert "- Profit = revenue - cost.\n" in sql_system
    assert "Money is in euros (EUR)." in sql_system
    assert "complete from 2023-01-01 to about 2023-12-31" in sql_system

    explain = build_explain_system(profile)
    assert "Money is in euros (EUR): write amounts like EUR 1,234.56" in explain
    assert "the data ends in Dec 2023" in explain
    assert '"excluding refunds", not "valid sales"' in explain


def test_dollar_format_does_not_forbid_the_dollar_sign():
    profile = DatasetProfile(name="x", currency="US dollars (USD)", currency_format="$1,234.56")
    explain = build_explain_system(profile)
    assert "write amounts like $1,234.56." in explain
    assert "never with a plain $ sign" not in explain


@pytest.mark.parametrize("profile", [None, OLIST_PROFILE, DatasetProfile(name="x")])
def test_explanation_names_the_actual_period(profile):
    explain = build_explain_system(profile)
    assert 'If the question uses a relative period (e.g. "last month"' in explain
    assert 'name the actual period\nthe result covers (e.g. "May 2023")' in explain
    assert "Never guess a\nperiod that does not appear there" in explain
    # The SQL must return the resolved period, otherwise the explanation cannot see it.
    assert "also return the period it resolves to" in build_sql_system(profile)


def test_generic_examples_show_returning_the_resolved_month():
    assert "Question: How many sales were made last month?" in EXAMPLES
    assert "AS month, COUNT(*) AS sales" in EXAMPLES


def test_currency_without_format():
    explain = build_explain_system(DatasetProfile(name="x", currency="points"))
    assert "Money is in points.\n" in explain
