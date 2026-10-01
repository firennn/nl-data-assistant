"""Executive summary for the weekly report (owner: owner2).

The LLM only sees a short list of computed facts (metrics, flagged changes, anomalies, top
category and state, forecast), never raw rows. Its reply is then checked: every number in the
summary must appear in the facts (allowing for rounding). If the reply contains a number that is
not in the facts, or the LLM call fails, a plain summary built from the same facts is used
instead, so the report never shows an invented figure.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass

import pandas as pd

from reports.anomalies import AnomalyConfig
from shared.llm import LLMError, LLMProvider, get_llm
from shared.models import MetricResult

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a data analyst writing the executive summary of a weekly e-commerce \
report for managers.
Rules:
- Use only the facts provided. Do not compute new numbers and do not invent figures.
- Quote the numbers you use exactly as written in the facts.
- Write 4 to 6 plain sentences in one paragraph: the main results, notable changes or
  anomalies, the forecast with its range, and one or two concrete recommendations.
- If the facts say the forecast model does not beat the naive baseline, say so plainly.
- No headings, no bullet points, no markdown."""

# Small whole numbers ("two weeks", "4 weeks", "top 10") are allowed without appearing in facts.
SMALL_NUMBERS_LIMIT = 10
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


@dataclass
class Summary:
    text: str
    source: str  # "llm" or "fallback"
    facts: str  # the facts the summary is based on


def format_number(value: float, unit: str | None = None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    if unit == "BRL":
        return f"{value:,.2f} BRL"
    if unit == "%":
        return f"{value:.1f}%"
    if unit == "score":
        return f"{value:.2f}"
    return f"{value:,.0f}"


def build_facts(
    *,
    metrics: list[MetricResult],
    metric_changes: pd.DataFrame | None = None,
    anomalies: pd.DataFrame | None = None,
    top_category: tuple[str, float] | None = None,
    top_state: tuple[str, int] | None = None,
    forecast: pd.DataFrame | None = None,
) -> str:
    """Turn the computed results into short plain-text facts for the summary prompt."""
    lines = []
    if metrics:
        first = metrics[0]
        lines.append(f"Report week: {first.period_start} to {first.period_end}.")
    for m in metrics:
        line = f"{m.name}: {format_number(m.value, m.unit)}"
        if m.comparison_value is not None:
            line += f" (previous week {format_number(m.comparison_value, m.unit)}"
            if m.change_pct is not None:
                line += f", change {m.change_pct:+.1f}%"
            line += ")"
        lines.append(line + ".")
    if metric_changes is not None and not metric_changes.empty:
        moved = ", ".join(f"{r.metric} {r.change_pct:+.1f}%" for r in metric_changes.itertuples())
        lines.append(f"Large week-over-week changes: {moved}.")
    else:
        threshold = AnomalyConfig().change_threshold_pct
        lines.append(
            f"No metric changed by {threshold:g}% or more compared with the previous week."
        )
    if anomalies is not None and not anomalies.empty:
        for r in anomalies.itertuples():
            lines.append(
                f"Anomaly on {r.date}: {r.metric} was {format_number(r.value)} "
                f"against an expected {format_number(r.expected)} (z-score {r.z_score:.1f})."
            )
    else:
        lines.append("No daily anomalies in revenue or orders this week.")
    if top_category:
        name, revenue = top_category
        lines.append(f"Top category by revenue: {name} ({format_number(revenue, 'BRL')}).")
    if top_state:
        state, orders = top_state
        lines.append(f"State with the most orders: {state} ({orders:,} orders).")
    if forecast is not None and not forecast.empty:
        lines.extend(_forecast_facts(forecast))
    return "\n".join(f"- {line}" for line in lines)


def _forecast_facts(forecast: pd.DataFrame) -> list[str]:
    first = forecast.iloc[0]
    lines = [
        f"Revenue forecast for the week starting {first['week']}: "
        f"{format_number(first['forecast'], 'BRL')} (80% range "
        f"{format_number(first['lower'], 'BRL')} to {format_number(first['upper'], 'BRL')})."
    ]
    model = forecast.attrs.get("model")
    scores = forecast.attrs.get("scores") or {}
    if model and "naive" in scores and model in scores:
        mae_model = format_number(scores[model]["mae"], "BRL")
        mae_naive = format_number(scores["naive"]["mae"], "BRL")
        if forecast.attrs.get("beats_baseline"):
            lines.append(
                f"The forecast model beats the naive baseline in backtesting "
                f"(average error {mae_model} vs {mae_naive})."
            )
        else:
            lines.append(
                f"The forecast model does not beat the naive baseline (average error "
                f"{mae_naive}), so the naive forecast (last week's revenue) is used."
            )
    elif model:
        lines.append("There is too little history to test the forecast; it repeats last week.")
    return lines


def unsupported_numbers(summary: str, facts: str) -> list[str]:
    """Return the numbers in `summary` that do not match any number in `facts`.

    A summary number matches if it equals a fact number after rounding (for example 244,676
    for 244,675.59) or is within 0.5% of it. Signs are ignored, since "fell 11.3%" quotes
    "-11.3%". Small whole numbers up to SMALL_NUMBERS_LIMIT are always allowed.
    """
    allowed = [_to_float(n) for n in _NUMBER.findall(facts)]
    unsupported = []
    for token in _NUMBER.findall(summary):
        value = _to_float(token)
        if value.is_integer() and value <= SMALL_NUMBERS_LIMIT:
            continue
        if not any(_matches(value, fact) for fact in allowed):
            unsupported.append(token)
    return unsupported


def _to_float(token: str) -> float:
    return float(token.replace(",", ""))


def _matches(value: float, fact: float) -> bool:
    if any(round(fact, digits) == value for digits in (0, 1, 2)):
        return True
    return fact != 0 and abs(value - fact) / abs(fact) <= 0.005


def fallback_summary(facts: str) -> str:
    """A plain summary that only restates the facts."""
    sentences = [line.removeprefix("- ") for line in facts.splitlines()]
    return " ".join(sentences)


def write_summary(facts: str, llm: LLMProvider | None = None) -> Summary:
    """Ask the LLM for the executive summary and check it against the facts."""
    prompt = f"Facts for this week's report:\n{facts}\n\nWrite the executive summary."
    try:
        llm = llm or get_llm()
        reply = llm.complete(prompt, system=SYSTEM_PROMPT, temperature=0.2, max_tokens=1024)
        text = reply.text.strip()
    except LLMError as exc:
        logger.warning("Summary LLM call failed, using the plain summary: %s", exc)
        return Summary(fallback_summary(facts), "fallback", facts)
    if not text:
        return Summary(fallback_summary(facts), "fallback", facts)
    unsupported = unsupported_numbers(text, facts)
    if unsupported:
        logger.warning(
            "Summary used numbers not in the facts %s; using the plain summary.", unsupported
        )
        return Summary(fallback_summary(facts), "fallback", facts)
    return Summary(text, "llm", facts)
