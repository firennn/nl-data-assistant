"""Data models shared by the agent, report, evaluation and dashboard modules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

import pandas as pd

ChartKind = Literal["bar", "line", "scatter", "pie", "table", "metric"]


@dataclass
class ChartSpec:
    """Library-independent description of a chart. Render it with shared.charts.render_chart."""

    kind: ChartKind
    title: str = ""
    x: str | None = None
    y: str | list[str] | None = None
    color: str | None = None


@dataclass
class QueryResult:
    """Outcome of answering one natural-language question.

    Exactly one of these holds:
    - ok: sql ran and `data` holds the result
    - needs_clarification: the question was ambiguous; show `clarifying_question`
    - error is set: the question could not be answered (unsafe SQL, repeated failures, ...)
    """

    question: str
    sql: str | None = None
    data: pd.DataFrame | None = None
    chart: ChartSpec | None = None
    explanation: str = ""
    error: str | None = None
    needs_clarification: bool = False
    clarifying_question: str | None = None
    attempts: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None and not self.needs_clarification and self.data is not None


@dataclass
class MetricResult:
    """A single business metric for a period, optionally compared with an earlier period."""

    name: str
    value: float
    period_start: date
    period_end: date
    comparison_value: float | None = None
    comparison_label: str | None = None  # e.g. "previous week"
    unit: str | None = None  # e.g. "BRL", "orders", "%"

    @property
    def change_pct(self) -> float | None:
        """Percent change versus the comparison value, or None if not comparable."""
        if self.comparison_value is None or self.comparison_value == 0:
            return None
        return (self.value - self.comparison_value) / abs(self.comparison_value) * 100
