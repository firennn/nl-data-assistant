"""Run the evaluation set against the agent and summarize accuracy and failure patterns
(owner: owner3).

The agent is passed in as a function (question -> QueryResult), so the evaluation does not
depend on agent internals. Typical use:

    from agent import SQLAgent
    report = run_evaluation(SQLAgent().ask, load_questions())
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from shared.models import QueryResult

QUESTIONS_PATH = Path(__file__).resolve().parent / "questions.json"

CATEGORIES = ("filter", "aggregation", "join", "time", "ranking", "ambiguous", "unsafe")
DIFFICULTIES = ("easy", "medium", "hard")
# answer: the agent must return the gold result.
# clarify_or_answer: an ambiguous question; asking a clarifying question is correct, and so is
#   answering with the gold result or one of the alt_sql readings.
# refuse: a request to change data; only a refusal is correct and there is no gold SQL.
EXPECTS = ("answer", "clarify_or_answer", "refuse")
REQUIRED_FIELDS = ("id", "question", "gold_sql", "category", "difficulty", "ordered")


class QuestionFileError(ValueError):
    """The questions file is missing fields or has invalid values."""


@dataclass
class EvalCase:
    id: str
    question: str
    gold_sql: str  # empty for questions the agent must refuse
    category: str
    difficulty: str  # easy | medium | hard
    ordered: bool = False  # whether row order matters when comparing results
    expect: str = "answer"  # answer | clarify_or_answer | refuse
    alt_sql: list[str] = field(default_factory=list)  # other valid readings (ambiguous only)
    check_sql: str | None = None  # independently written query used to verify gold_sql
    skills: list[str] = field(default_factory=list)  # e.g. revenue_rule, multi_join
    expected: dict | None = None  # {"columns": [...], "rows": [[...]]} from verified gold_sql
    notes: str = ""  # why the question is in the set


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

    The questions are a held-out test set: never use them (or their SQL) as prompt examples
    or training data. See evaluation/README.md.

    Raises QuestionFileError with the question id and the problem if the file is invalid.
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise QuestionFileError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, list) or not raw:
        raise QuestionFileError(f"{path} must contain a non-empty list of questions")

    known = {f.name for f in EvalCase.__dataclass_fields__.values()}
    cases: list[EvalCase] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        label = item.get("id", f"question #{i + 1}") if isinstance(item, dict) else f"#{i + 1}"
        if not isinstance(item, dict):
            raise QuestionFileError(f"{label}: each question must be a JSON object")
        missing = [name for name in REQUIRED_FIELDS if name not in item]
        if missing:
            raise QuestionFileError(f"{label}: missing field(s) {', '.join(missing)}")
        unknown = sorted(set(item) - known)
        if unknown:
            raise QuestionFileError(f"{label}: unknown field(s) {', '.join(unknown)}")
        case = EvalCase(**item)
        _validate_case(case, seen)
        seen.add(case.id)
        cases.append(case)
    return cases


def _validate_case(case: EvalCase, seen: set[str]) -> None:
    def fail(problem: str) -> None:
        raise QuestionFileError(f"{case.id}: {problem}")

    if not case.id or not isinstance(case.id, str):
        fail("id must be a non-empty string")
    if case.id in seen:
        fail("duplicate id")
    if not case.question.strip():
        fail("question is empty")
    if case.category not in CATEGORIES:
        fail(f"category must be one of {', '.join(CATEGORIES)}")
    if case.difficulty not in DIFFICULTIES:
        fail(f"difficulty must be one of {', '.join(DIFFICULTIES)}")
    if case.expect not in EXPECTS:
        fail(f"expect must be one of {', '.join(EXPECTS)}")
    if not isinstance(case.ordered, bool):
        fail("ordered must be true or false")
    if case.expect == "refuse":
        if case.gold_sql or case.alt_sql or case.check_sql:
            fail("a question the agent must refuse has no gold_sql, alt_sql or check_sql")
    elif not case.gold_sql.strip():
        fail("gold_sql is empty")
    if case.alt_sql and case.expect != "clarify_or_answer":
        fail("alt_sql is only used for ambiguous (clarify_or_answer) questions")
    if case.expected is not None and set(case.expected) != {"columns", "rows"}:
        fail('expected must have exactly the keys "columns" and "rows"')


def run_evaluation(ask: Callable[[str], QueryResult], cases: list[EvalCase]) -> EvalReport:
    """Ask every question, run the gold SQL, compare results and classify failures.

    TODO(owner3):
    - Run gold_sql with shared.db.run_query; compare with evaluation.compare.results_match.
    - Record latency, attempts and a failure_type for every incorrect case.
    - Save a JSON/CSV of the records under evaluation/results/ for the dashboard.
    - Provide `python -m evaluation.run_eval` that prints accuracy overall and by category.
    """
    raise NotImplementedError
