"""Verify the evaluation questions against the database (owner: owner3).

For every question that has SQL, this runs the gold query, the independently written check
query and any alternative readings, and confirms that:
- the gold and check queries return the same data (so the gold answer is not a one-off mistake),
- every alternative reading runs,
- the gold result still equals the expected result stored in questions.json.

Usage:
    python -m evaluation.verify_questions                  # verify, exit code 1 on any problem
    python -m evaluation.verify_questions --write-expected  # also store gold results as expected

`--write-expected` only stores a result when the gold and check queries agree.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import pandas as pd

from evaluation.compare import compare_results
from evaluation.run_eval import QUESTIONS_PATH, EvalCase, load_questions
from shared.db import QueryError, UnsafeQueryError, run_query

MAX_EXPECTED_ROWS = 50  # questions are designed to return small results


def result_to_expected(df: pd.DataFrame) -> dict:
    """Store a result as JSON-friendly columns and rows (NaN becomes null)."""

    def clean(value: object) -> object:
        if hasattr(value, "item"):  # numpy scalars
            value = value.item()
        if isinstance(value, float) and math.isnan(value):
            return None
        return value

    rows = [[clean(v) for v in row] for row in df.itertuples(index=False, name=None)]
    return {"columns": [str(c) for c in df.columns], "rows": rows}


def expected_to_frame(expected: dict) -> pd.DataFrame:
    return pd.DataFrame(expected["rows"], columns=expected["columns"])


def verify_case(case: EvalCase, *, db_path: Path | None = None) -> tuple[list[str], dict | None]:
    """Return (problems, gold result as expected) for one question."""
    if case.expect == "refuse":
        return [], None

    problems: list[str] = []

    def run(sql: str, label: str) -> pd.DataFrame | None:
        try:
            return run_query(sql, db_path=db_path, max_rows=MAX_EXPECTED_ROWS + 1)
        except (QueryError, UnsafeQueryError) as exc:
            problems.append(f"{label} failed: {exc}")
            return None

    gold = run(case.gold_sql, "gold_sql")
    if gold is None:
        return problems, None
    if len(gold) > MAX_EXPECTED_ROWS:
        problems.append(f"gold_sql returns more than {MAX_EXPECTED_ROWS} rows")

    if case.check_sql:
        check = run(case.check_sql, "check_sql")
        if check is not None:
            outcome = compare_results(check, gold, ordered=case.ordered, allow_extra_columns=False)
            if not outcome.match:
                problems.append(f"check_sql disagrees with gold_sql ({outcome.reason})")
    else:
        problems.append("no check_sql to verify gold_sql")

    for i, sql in enumerate(case.alt_sql, 1):
        run(sql, f"alt_sql #{i}")

    if case.expected is not None:
        outcome = compare_results(
            gold, expected_to_frame(case.expected), ordered=case.ordered, allow_extra_columns=False
        )
        if not outcome.match:
            problems.append(f"gold result differs from stored expected ({outcome.reason})")

    return problems, result_to_expected(gold)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the evaluation questions.")
    parser.add_argument("--db", type=Path, default=None, help="database (default: DB_PATH)")
    parser.add_argument("--questions", type=Path, default=QUESTIONS_PATH)
    parser.add_argument(
        "--write-expected",
        action="store_true",
        help="store the gold result as expected for questions that pass verification",
    )
    args = parser.parse_args(argv)

    cases = load_questions(args.questions)
    results: dict[str, dict] = {}
    failed = 0
    for case in cases:
        problems, expected = verify_case(case, db_path=args.db)
        if case.expect == "refuse":
            status = "ok (refuse, no SQL)"
        elif problems:
            status = "FAIL: " + "; ".join(problems)
            failed += 1
        else:
            rows = len(expected["rows"]) if expected else 0
            status = f"ok ({rows} row{'s' if rows != 1 else ''})"
        print(f"{case.id:6} {case.category:12} {status}")
        if expected is not None and not problems:
            results[case.id] = expected

    if args.write_expected:
        raw = json.loads(args.questions.read_text(encoding="utf-8"))
        for item in raw:
            if item["id"] in results:
                item["expected"] = results[item["id"]]
        args.questions.write_text(
            json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"Stored expected results for {len(results)} question(s).")

    print(f"{len(cases) - failed}/{len(cases)} questions verified.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
