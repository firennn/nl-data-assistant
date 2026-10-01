import json

import pandas as pd

from evaluation.compare import results_match
from evaluation.run_eval import QUESTIONS_PATH, load_questions, run_evaluation

REQUIRED_FIELDS = {"id", "question", "gold_sql", "category", "difficulty", "ordered"}


def test_public_interface_exists():
    assert callable(results_match) and callable(load_questions) and callable(run_evaluation)


def test_questions_file_format():
    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    assert questions, "questions.json is empty"
    for q in questions:
        assert REQUIRED_FIELDS <= q.keys(), q.get("id")


def test_results_match():
    """Same values with different column names/order match; different values do not."""
    gold = pd.DataFrame({"category": ["a", "b"], "items": [3, 5]})
    renamed = pd.DataFrame({"items_sold": [5, 3], "cat": ["b", "a"]})
    assert results_match(renamed, gold)
    assert not results_match(pd.DataFrame({"c": ["a", "b"], "n": [3, 6]}), gold)
