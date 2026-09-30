"""Run the evaluation set against the agent and summarize accuracy and failure patterns
(owner: owner3).

The agent is passed in as a function (question -> QueryResult), so the evaluation does not
depend on agent internals. Typical use:

    from agent import SQLAgent
    report = run_evaluation(SQLAgent().ask, load_questions())
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from shared.models import QueryResult

QUESTIONS_PATH = Path(__file__).resolve().parent / "questions.json"


@dataclass
class EvalCase:
    id: str
    question: str
    gold_sql: str
    category: str
    difficulty: str  # easy | medium | hard
    ordered: bool = False  # whether row order matters when comparing results


@dataclass
class EvalRecord:
    case: EvalCase
    predicted_sql: str | None
    correct: bool
    failure_type: str | None = None  # e.g. "sql_error", "wrong_result", "clarification", "unsafe"
    attempts: int = 0
    latency_s: float = 0.0


@dataclass
class EvalReport:
    records: list[EvalRecord] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return sum(r.correct for r in self.records) / len(self.records) if self.records else 0.0


def load_questions(path: Path = QUESTIONS_PATH) -> list[EvalCase]:
    """Load and validate the evaluation questions.

    TODO(owner3):
    - Parse the JSON into EvalCase objects; fail clearly on missing fields or duplicate ids.
    - Grow the set to 20-30 questions covering: simple counts, filters, joins, date grouping,
      ranking, ambiguous questions (expected to trigger a clarification) and at least one
      question that tempts an unsafe query.
    """
    raise NotImplementedError


def run_evaluation(ask: Callable[[str], QueryResult], cases: list[EvalCase]) -> EvalReport:
    """Ask every question, run the gold SQL, compare results and classify failures.

    TODO(owner3):
    - Run gold_sql with shared.db.run_query; compare with evaluation.compare.results_match.
    - Record latency, attempts and a failure_type for every incorrect case.
    - Save a JSON/CSV of the records under evaluation/results/ for the dashboard.
    - Provide `python -m evaluation.run_eval` that prints accuracy overall and by category.
    """
    raise NotImplementedError
