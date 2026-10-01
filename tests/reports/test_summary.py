"""Executive summary: facts, the number check and the fallback, with a scripted LLM."""

import math
import re
from datetime import date

import pandas as pd

from reports.summary import (
    SYSTEM_PROMPT,
    build_facts,
    fallback_summary,
    unsupported_numbers,
    write_summary,
)
from shared.llm import FakeProvider, LLMError, LLMRateLimitError
from shared.models import MetricResult

WEEK = (date(2018, 8, 13), date(2018, 8, 19))


def metric(name, value, previous, unit):
    return MetricResult(name, value, *WEEK, previous, "previous week", unit)


METRICS = [
    metric("Revenue", 244675.59, 275772.04, "BRL"),
    metric("Orders", 1852.0, 1961.0, "orders"),
    metric("Average review score", math.nan, None, "score"),
]


def forecast(model="holt_damped", beats=True):
    df = pd.DataFrame(
        {
            "week": [date(2018, 8, 20)],
            "forecast": [254632.0],
            "lower": [202212.0],
            "upper": [307052.0],
        }
    )
    df.attrs.update(
        model=model,
        beats_baseline=beats,
        scores={"naive": {"mae": 40601.0}, "holt_damped": {"mae": 39085.0}},
    )
    return df


FACTS = build_facts(
    metrics=METRICS,
    top_category=("health_beauty", 31551.34),
    top_state=("SP", 908),
    forecast=forecast(),
)


def test_facts_contain_the_computed_numbers():
    assert "Report week: 2018-08-13 to 2018-08-19." in FACTS
    assert "Revenue: 244,675.59 BRL (previous week 275,772.04 BRL, change -11.3%)." in FACTS
    assert "Orders: 1,852 (previous week 1,961, change -5.6%)." in FACTS
    assert "Average review score: n/a." in FACTS
    assert "health_beauty (31,551.34 BRL)" in FACTS
    assert "SP (908 orders)" in FACTS
    assert "254,632.00 BRL (80% range 202,212.00 BRL to 307,052.00 BRL)" in FACTS
    assert "beats the naive baseline" in FACTS
    assert "No daily anomalies" in FACTS


def test_facts_say_plainly_when_the_model_does_not_beat_the_baseline():
    facts = build_facts(metrics=METRICS, forecast=forecast(model="naive", beats=False))
    assert "does not beat the naive baseline" in facts


def test_facts_list_anomalies_and_large_changes():
    anomalies = pd.DataFrame(
        {
            "date": [date(2018, 8, 15)],
            "metric": ["Orders"],
            "value": [600.0],
            "expected": [280.0],
            "z_score": [4.2],
            "method": ["rolling z-score"],
        }
    )
    changes = pd.DataFrame(
        {
            "metric": ["Revenue"],
            "value": [1.0],
            "previous": [2.0],
            "change_pct": [-50.0],
            "unit": ["BRL"],
        }
    )
    facts = build_facts(metrics=METRICS, anomalies=anomalies, metric_changes=changes)
    assert "Anomaly on 2018-08-15: Orders was 600 against an expected 280 (z-score 4.2)." in facts
    assert "Large week-over-week changes: Revenue -50.0%." in facts


def test_numbers_from_the_facts_are_accepted_including_rounding():
    summary = (
        "Revenue fell 11.3% to 244,675.59 BRL (about 244,676) on 1,852 orders, and the "
        "forecast for the week of 2018-08-20 is 254,632 BRL with a range of 202,212 to "
        "307,052 over the next 4 weeks."
    )
    assert unsupported_numbers(summary, FACTS) == []


def test_invented_numbers_are_caught():
    summary = "Revenue fell 11.3% to 244,675.59 BRL, and conversion was 3.7% from 52,000 visits."
    assert unsupported_numbers(summary, FACTS) == ["3.7", "52,000"]


def test_good_llm_summary_is_used():
    text = "Revenue fell 11.3% to 244,675.59 BRL while orders dropped to 1,852."
    llm = FakeProvider([text])
    summary = write_summary(FACTS, llm)
    assert summary.source == "llm"
    assert summary.text == text
    prompt, system = llm.calls[0]
    assert FACTS in prompt
    assert system == SYSTEM_PROMPT


def test_summary_with_an_invented_number_falls_back_to_the_plain_summary():
    summary = write_summary(FACTS, FakeProvider(["Revenue jumped to 999,999 BRL."]))
    assert summary.source == "fallback"
    assert summary.text == fallback_summary(FACTS)
    assert "999,999" not in summary.text


def test_llm_failures_and_empty_replies_fall_back():
    for reply in (LLMError("no key"), LLMRateLimitError("daily quota"), "   "):
        summary = write_summary(FACTS, FakeProvider([reply]))
        assert summary.source == "fallback"
        assert "244,675.59 BRL" in summary.text


def test_fallback_summary_only_restates_the_facts():
    text = fallback_summary(FACTS)
    assert not text.startswith("-")
    assert unsupported_numbers(text, FACTS) == []


def test_prompt_contains_no_raw_rows(sample_db):
    from reports.weekly_report import build_report

    llm = FakeProvider(["Revenue was 405.80 BRL."])
    build_report(date(2018, 1, 7), db_path=sample_db, llm=llm)
    prompt, _ = llm.calls[0]
    for raw_id in ("o1", "o2", "c1", "u1", "p1", "s1"):
        assert not re.search(rf"\b{raw_id}\b", prompt)
