import json
import re
from collections import Counter
from pathlib import Path

import pytest

from evaluation import verify_questions
from evaluation.run_eval import (
    CATEGORIES,
    QUESTIONS_PATH,
    QuestionFileError,
    load_questions,
)

ROOT = Path(__file__).resolve().parents[2]
PROMPT_FILES = [ROOT / "agent" / "prompts.py", ROOT / "shared" / "profiles.py"]


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def write_questions(tmp_path: Path, questions: list[dict]) -> Path:
    path = tmp_path / "questions.json"
    path.write_text(json.dumps(questions), encoding="utf-8")
    return path


def question(**overrides) -> dict:
    base = {
        "id": "t1",
        "question": "How many orders are there?",
        "gold_sql": "SELECT COUNT(*) FROM orders",
        "category": "filter",
        "difficulty": "easy",
        "ordered": False,
    }
    return {**base, **overrides}


# ---- the real question set


def test_question_set_loads():
    cases = load_questions()
    assert 20 <= len(cases) <= 30


def test_every_category_is_covered():
    counts = Counter(c.category for c in load_questions())
    assert set(counts) == set(CATEGORIES)
    assert counts["ambiguous"] >= 3
    assert counts["unsafe"] >= 2


def test_answerable_questions_have_check_sql_and_expected_rows():
    for case in load_questions():
        if case.expect == "refuse":
            assert case.expected is None, case.id
        else:
            assert case.check_sql, case.id
            assert case.expected and case.expected["rows"], case.id


def test_ambiguous_questions_allow_clarification():
    for case in load_questions():
        if case.category == "ambiguous":
            assert case.expect == "clarify_or_answer", case.id
        if case.category == "unsafe":
            assert case.expect == "refuse", case.id


def test_questions_are_not_used_in_prompts():
    """The held-out questions and their SQL must not leak into prompts or profiles."""
    prompt_text = normalize(" ".join(p.read_text(encoding="utf-8") for p in PROMPT_FILES))
    for case in load_questions():
        assert normalize(case.question) not in prompt_text, case.id
        for sql in [case.gold_sql, case.check_sql, *case.alt_sql]:
            if sql:
                assert normalize(sql) not in prompt_text, case.id


# ---- load_questions validation


def test_load_valid_file(tmp_path):
    cases = load_questions(write_questions(tmp_path, [question()]))
    assert cases[0].id == "t1"
    assert cases[0].expect == "answer"


@pytest.mark.parametrize(
    ("questions", "message"),
    [
        ([question(id="a"), question(id="a")], "duplicate id"),
        ([{k: v for k, v in question().items() if k != "gold_sql"}], "missing field"),
        ([question(colour="red")], "unknown field"),
        ([question(category="other")], "category must be one of"),
        ([question(difficulty="trivial")], "difficulty must be one of"),
        ([question(expect="maybe")], "expect must be one of"),
        ([question(ordered="yes")], "ordered must be true or false"),
        ([question(gold_sql=" ")], "gold_sql is empty"),
        ([question(expect="refuse", category="unsafe")], "must refuse has no gold_sql"),
        ([question(alt_sql=["SELECT 1"])], "alt_sql is only used"),
        ([question(expected={"rows": []})], "expected must have"),
        ([], "non-empty list"),
    ],
)
def test_load_rejects_invalid_files(tmp_path, questions, message):
    with pytest.raises(QuestionFileError, match=message):
        load_questions(write_questions(tmp_path, questions))


def test_load_rejects_invalid_json(tmp_path):
    path = tmp_path / "questions.json"
    path.write_text("[{", encoding="utf-8")
    with pytest.raises(QuestionFileError, match="not valid JSON"):
        load_questions(path)


# ---- verify_questions on the fixture database


def test_verify_accepts_matching_check_sql(sample_db, tmp_path):
    q = question(check_sql="SELECT SUM(1) FROM orders")
    case = load_questions(write_questions(tmp_path, [q]))[0]
    problems, expected = verify_questions.verify_case(case, db_path=sample_db)
    assert problems == []
    assert expected == {"columns": ["COUNT(*)"], "rows": [[3]]}


def test_verify_reports_disagreeing_check_sql(sample_db, tmp_path):
    q = question(check_sql="SELECT COUNT(*) FROM orders WHERE status = 'delivered'")
    case = load_questions(write_questions(tmp_path, [q]))[0]
    problems, _ = verify_questions.verify_case(case, db_path=sample_db)
    assert any("check_sql disagrees" in p for p in problems)


def test_verify_reports_changed_expected_result(sample_db, tmp_path):
    q = question(check_sql="SELECT SUM(1) FROM orders", expected={"columns": ["n"], "rows": [[4]]})
    case = load_questions(write_questions(tmp_path, [q]))[0]
    problems, _ = verify_questions.verify_case(case, db_path=sample_db)
    assert any("differs from stored expected" in p for p in problems)


def test_verify_reports_broken_sql(sample_db, tmp_path):
    q = question(check_sql="SELECT SUM(1) FROM orders", alt_sql=["SELECT nope FROM orders"])
    q.update(category="ambiguous", expect="clarify_or_answer")
    case = load_questions(write_questions(tmp_path, [q]))[0]
    problems, _ = verify_questions.verify_case(case, db_path=sample_db)
    assert any("alt_sql #1 failed" in p for p in problems)


def test_verify_main_writes_expected(sample_db, tmp_path, capsys):
    path = write_questions(tmp_path, [question(check_sql="SELECT SUM(1) FROM orders")])
    assert verify_questions.main(["--db", str(sample_db), "--questions", str(path)]) == 0
    assert "expected" not in json.loads(path.read_text(encoding="utf-8"))[0]
    verify_questions.main(["--db", str(sample_db), "--questions", str(path), "--write-expected"])
    assert json.loads(path.read_text(encoding="utf-8"))[0]["expected"]["rows"] == [[3]]
    assert "1/1 questions verified" in capsys.readouterr().out


@pytest.mark.skipif(
    not (ROOT / "data" / "olist.db").exists(), reason="needs the full database (data.build_db)"
)
def test_question_set_verifies_on_full_database():
    assert verify_questions.main(["--questions", str(QUESTIONS_PATH)]) == 0
