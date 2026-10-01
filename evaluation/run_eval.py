"""Run the evaluation set against the agent and summarize accuracy and failure patterns
(owner: owner3).

The agent is passed in as a function (question -> QueryResult), so the evaluation does not
depend on agent internals and can score any agent version, provider or model. Typical use:

    from agent import SQLAgent
    report = run_evaluation(SQLAgent().ask, load_questions())

Command line (results are saved to evaluation/results/ after every question):

    python -m evaluation.run_eval                          # configured provider, Olist profile
    python -m evaluation.run_eval --provider groq          # another provider from .env
    python -m evaluation.run_eval --profile no-rules       # Olist without its business rules
    python -m evaluation.run_eval --resume <results file>  # finish an interrupted run
    python -m evaluation.run_eval --compare a.json b.json  # compare runs side by side
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path

from evaluation import metrics
from evaluation.compare import compare_results
from shared.config import Settings, get_settings
from shared.db import QueryError, UnsafeQueryError, run_query
from shared.models import DatasetProfile, QueryResult
from shared.profiles import OLIST_PROFILE

QUESTIONS_PATH = Path(__file__).resolve().parent / "questions.json"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

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


# Why a prediction failed. llm_error and gold_error are not the agent's fault: those questions
# are left out of the accuracy ("not scored") and can be asked again with --resume.
FAILURE_TYPES = (
    "sql_error",  # no working query after the agent's retries
    "wrong_values",  # same shape, different data
    "wrong_row_count",
    "missing_columns",  # a requested column is missing
    "row_order",  # right rows, wrong order (ranking questions)
    "unnecessary_clarification",  # asked a question although the meaning is clear
    "wrongly_refused",  # refused an ordinary question
    "not_refused",  # did not refuse a request to change data
    "agent_error",  # ask() raised an exception
    "llm_error",  # the LLM provider was unavailable (quota, outage); not scored
    "gold_error",  # the reference query failed; not scored
)
UNSCORED_FAILURES = ("llm_error", "gold_error")

# The agent reports refusals and LLM outages as error messages. These prefixes match the
# messages of agent.SQLAgent.ask.
REFUSAL_PREFIXES = ("Cannot answer:", "Blocked unsafe query")
LLM_ERROR_PREFIX = "The language model is not available"

GOLD_MAX_ROWS = 10_000


@dataclass
class EvalRecord:
    case: EvalCase
    predicted_sql: str | None
    correct: bool
    failure_type: str | None = None  # one of FAILURE_TYPES, None when correct
    attempts: int = 0
    latency_s: float = 0.0
    outcome: str = ""  # answered | clarified | refused | error | llm_error
    reason: str = ""  # details of a failure, e.g. "wrong row count (3 instead of 5)"
    matched: str | None = None  # gold, alt_sql #n, clarification or refusal
    extra_columns: int = 0  # predicted columns beyond the requested ones
    rows: int | None = None  # rows in the predicted result
    error: str | None = None
    clarifying_question: str | None = None

    @property
    def scored(self) -> bool:
        return self.failure_type not in UNSCORED_FAILURES

    def to_dict(self) -> dict:
        """Flat record for the results file and the dashboard."""
        case = self.case
        data = asdict(self)
        del data["case"]
        return {
            "id": case.id,
            "question": case.question,
            "category": case.category,
            "difficulty": case.difficulty,
            "expect": case.expect,
            "skills": list(case.skills),
            "gold_sql": case.gold_sql,
            "scored": self.scored,
            **data,
        }

    @classmethod
    def from_dict(cls, data: dict, case: EvalCase) -> EvalRecord:
        names = set(cls.__dataclass_fields__) - {"case"}
        return cls(case=case, **{k: v for k, v in data.items() if k in names})


@dataclass
class EvalReport:
    records: list[EvalRecord] = field(default_factory=list)
    meta: dict = field(default_factory=dict)  # label, provider, model, profile, dates, ...

    @property
    def scored(self) -> list[EvalRecord]:
        return [r for r in self.records if r.scored]

    @property
    def accuracy(self) -> float:
        """Share of scored questions answered correctly (LLM outages are not counted)."""
        scored = self.scored
        return sum(r.correct for r in scored) / len(scored) if scored else 0.0

    def to_dict(self) -> dict:
        rows = [r.to_dict() for r in self.records]
        return {"meta": self.meta, "summary": metrics.summarize(rows), "records": rows}


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


def run_evaluation(
    ask: Callable[[str], QueryResult],
    cases: list[EvalCase],
    *,
    db_path: str | Path | None = None,
    meta: dict | None = None,
    on_record: Callable[[EvalReport], None] | None = None,
    delay_s: float = 0.0,
) -> EvalReport:
    """Ask every question, compare the result with the reference queries and classify failures.

    Args:
        ask: the agent as a function (question -> QueryResult).
        cases: questions from load_questions().
        db_path: database used for the reference queries; must be the one the agent queries.
        meta: run details stored with the results (label, provider, model, profile, ...).
        on_record: called with the report after every question, e.g. to save progress.
        delay_s: pause between questions (to stay under per-minute rate limits).
    """
    report = EvalReport(meta=dict(meta or {}))
    for i, case in enumerate(cases):
        if i and delay_s:
            time.sleep(delay_s)
        start = time.perf_counter()
        crash = ""
        try:
            result = ask(case.question)
        except Exception as exc:  # the evaluation must survive a crashing agent
            result = None
            crash = f"{type(exc).__name__}: {exc}"
        latency = time.perf_counter() - start
        if result is None:
            record = EvalRecord(case, None, False, "agent_error", outcome="error", error=crash)
        else:
            record = score_result(case, result, db_path=db_path)
        record.latency_s = round(latency, 3)
        report.records.append(record)
        if on_record:
            on_record(report)
    return report


def classify_outcome(result: QueryResult) -> str:
    """answered | clarified | refused | llm_error | error."""
    if result.needs_clarification:
        return "clarified"
    if result.ok:
        return "answered"
    error = result.error or ""
    if error.startswith(LLM_ERROR_PREFIX):
        return "llm_error"
    if error.startswith(REFUSAL_PREFIXES):
        return "refused"
    return "error"


def score_result(
    case: EvalCase, result: QueryResult, *, db_path: str | Path | None = None
) -> EvalRecord:
    """Score one agent result against the question's expected behaviour and reference data."""
    outcome = classify_outcome(result)
    record = EvalRecord(
        case=case,
        predicted_sql=result.sql,
        correct=False,
        attempts=result.attempts,
        outcome=outcome,
        rows=None if result.data is None else len(result.data),
        error=result.error,
        clarifying_question=result.clarifying_question,
    )

    if outcome == "llm_error":
        record.failure_type = "llm_error"
    elif case.expect == "refuse":
        record.correct = outcome == "refused"
        record.matched = "refusal" if record.correct else None
        record.failure_type = None if record.correct else "not_refused"
    elif outcome == "clarified":
        record.correct = case.expect == "clarify_or_answer"
        record.matched = "clarification" if record.correct else None
        record.failure_type = None if record.correct else "unnecessary_clarification"
    elif outcome == "refused":
        record.failure_type = "wrongly_refused"
    elif outcome == "error":
        record.failure_type = "sql_error"
        record.reason = result.error or ""
    else:
        _score_answer(case, result, record, db_path)
    return record


def _score_answer(
    case: EvalCase, result: QueryResult, record: EvalRecord, db_path: str | Path | None
) -> None:
    readings = [("gold", case.gold_sql)]
    if case.expect == "clarify_or_answer":
        readings += [(f"alt_sql #{i}", sql) for i, sql in enumerate(case.alt_sql, 1)]

    gold_reason = ""
    for label, sql in readings:
        try:
            reference = run_query(sql, db_path=db_path, max_rows=GOLD_MAX_ROWS)
        except (QueryError, UnsafeQueryError) as exc:
            if label == "gold":
                record.failure_type, record.reason = "gold_error", str(exc)
                return
            continue
        outcome = compare_results(result.data, reference, ordered=case.ordered)
        if outcome.match:
            record.correct, record.matched = True, label
            record.extra_columns = outcome.extra_columns
            return
        if label == "gold":
            gold_reason = outcome.reason

    record.reason = gold_reason
    if gold_reason.startswith("too few columns"):
        record.failure_type = "missing_columns"
    elif gold_reason.startswith("wrong row count"):
        record.failure_type = "wrong_row_count"
    elif gold_reason == "row order differs":
        record.failure_type = "row_order"
    else:
        record.failure_type = "wrong_values"


# ---- results files


def save_report(report: EvalReport, path: str | Path) -> Path:
    """Write the report as JSON (atomically, so an interrupted run never leaves a broken file)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_results(path: str | Path) -> dict:
    """Read a results file: {"meta": ..., "summary": ..., "records": [...]}."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def list_results(results_dir: str | Path = RESULTS_DIR) -> list[Path]:
    """Results files, newest first."""
    folder = Path(results_dir)
    if not folder.exists():
        return []
    return sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def report_from_results(data: dict, cases: list[EvalCase]) -> EvalReport:
    """Rebuild a report from a results file (used to resume a run)."""
    by_id = {c.id: c for c in cases}
    records = [
        EvalRecord.from_dict(r, by_id[r["id"]]) for r in data.get("records", []) if r["id"] in by_id
    ]
    return EvalReport(records=records, meta=dict(data.get("meta", {})))


def questions_version(path: str | Path = QUESTIONS_PATH) -> str:
    """Short hash of the questions file, so results from different question sets are noticed."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]


# ---- command line

PROFILE_KEYS = ("olist", "no-rules")


def no_rules_profile() -> DatasetProfile:
    """The Olist profile without its business rules and examples (to measure what they add)."""
    return replace(
        OLIST_PROFILE,
        name="Olist without business rules",
        rules=[],
        examples="",
        filter_example=None,
    )


def eval_settings(provider: str | None = None, model: str | None = None) -> Settings:
    """Settings for one provider and model, with the fallback provider turned off.

    A run must measure a single model, so the fallback is never used. Keys come from .env:
    the primary provider's key, or the fallback provider's key when that provider is chosen.
    """
    settings = get_settings()
    provider = (provider or settings.llm_provider).lower()
    if provider == settings.llm_provider:
        api_key, default_model = settings.llm_api_key, settings.llm_model
    elif provider == settings.llm_fallback_provider:
        api_key, default_model = settings.llm_fallback_api_key, settings.llm_fallback_model
    else:
        raise SystemExit(
            f"No API key for provider '{provider}' in .env "
            "(configure it as LLM_PROVIDER or LLM_FALLBACK_PROVIDER)."
        )
    return replace(
        settings,
        llm_provider=provider,
        llm_model=model or default_model,
        llm_api_key=api_key,
        llm_fallback_provider=None,
        llm_fallback_model=None,
        llm_fallback_api_key=None,
    )


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", text.lower()).strip("-")


def _run(args: argparse.Namespace) -> int:
    from agent import SQLAgent

    cases = load_questions(args.questions)
    previous: EvalReport | None = None
    if args.resume:
        previous = report_from_results(load_results(args.resume), cases)
        meta = previous.meta
        provider, model, profile_key = meta["provider"], meta["model"], meta["profile_key"]
        out_path = Path(args.resume)
    else:
        provider, model, profile_key = args.provider, args.model, args.profile

    settings = eval_settings(provider, None if model == "default" else model)
    profile = OLIST_PROFILE if profile_key == "olist" else no_rules_profile()
    if previous is None:
        started = datetime.now()
        model_name = settings.llm_model or "default"
        label = args.label or f"{settings.llm_provider}-{model_name}-{profile_key}"
        meta = {
            "label": label,
            "provider": settings.llm_provider,
            "model": model_name,
            "profile": profile.name,
            "profile_key": profile_key,
            "max_retries": settings.agent_max_retries,
            "questions_version": questions_version(args.questions),
            "started_at": started.isoformat(timespec="seconds"),
        }
        out_path = Path(args.results_dir) / f"{started:%Y%m%d-%H%M%S}_{_slug(label)}.json"

    if args.ids:
        wanted = {i.strip() for i in args.ids.split(",")}
        cases = [c for c in cases if c.id in wanted]
    if args.limit:
        cases = cases[: args.limit]

    done = [r for r in previous.records if r.scored] if previous else []
    done_ids = {r.case.id for r in done}
    todo = [c for c in cases if c.id not in done_ids]
    print(f"{meta['label']}: {len(todo)} question(s) to ask, results in {out_path}")

    agent = SQLAgent(settings=settings, profile=profile)

    def save(report: EvalReport) -> None:
        merged = EvalReport(records=done + report.records, meta=report.meta)
        merged.meta["finished_at"] = datetime.now().isoformat(timespec="seconds")
        save_report(merged, out_path)
        last = report.records[-1]
        mark = "ok" if last.correct else ("--" if not last.scored else "XX")
        detail = last.matched or last.failure_type or ""
        print(f"  {mark} {last.case.id} {last.case.category:12} {detail} ({last.latency_s:.1f}s)")

    run_evaluation(
        agent.ask, todo, db_path=settings.db_path, meta=meta, on_record=save, delay_s=args.delay
    )
    if out_path.exists():
        print()
        print(metrics.format_summary(load_results(out_path)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the NL-to-SQL agent.")
    parser.add_argument("--provider", help="LLM provider (default: LLM_PROVIDER from .env)")
    parser.add_argument("--model", help="model name (default: the provider's model from .env)")
    parser.add_argument("--profile", choices=PROFILE_KEYS, default="olist")
    parser.add_argument("--label", help="name for this run (default: provider-model-profile)")
    parser.add_argument("--ids", help="only these question ids, comma-separated")
    parser.add_argument("--limit", type=int, help="only the first N questions")
    parser.add_argument("--delay", type=float, default=0.0, help="seconds between questions")
    parser.add_argument("--resume", type=Path, help="results file of a run to finish")
    parser.add_argument("--questions", type=Path, default=QUESTIONS_PATH)
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--summary", type=Path, help="print the summary of a results file")
    parser.add_argument("--compare", type=Path, nargs="+", help="compare results files")
    args = parser.parse_args(argv)

    if args.summary:
        print(metrics.format_summary(load_results(args.summary)))
        return 0
    if args.compare:
        print(metrics.format_comparison([load_results(p) for p in args.compare]))
        return 0
    return _run(args)


if __name__ == "__main__":
    sys.exit(main())
