# Evaluation

Measures how often the NL-to-SQL agent returns the right data for business questions with known
answers. A prediction is scored by the data it returns, not by its SQL text (see
`evaluation/compare.py` and docs/DECISIONS.md).

## Held-out test set

`questions.json` is a **held-out test set**. The questions, their SQL and their expected results
must never be used as prompt examples, few-shot examples, fine-tuning or training data, or
copied into any module's prompts or dataset profiles. Otherwise the evaluation would measure
memorization instead of how well the agent generalizes. `tests/evaluation/test_questions.py`
fails if a question or its gold SQL appears in `agent/prompts.py` or `shared/profiles.py`.

If you need examples for a prompt or for training, write new ones that are not in this file.

## The questions

27 questions over the Olist database, each with a verified answer:

| Category | Count | What it tests |
|---|---|---|
| `filter` | 4 | one table, WHERE conditions, NULL handling |
| `aggregation` | 4 | averages, percentages, the revenue rule, counting orders once |
| `join` | 5 | 2-4 tables, distinct customers, join fan-out |
| `time` | 5 | grouping by month or day, date arithmetic, comparing periods |
| `ranking` | 3 | top-N lists where the order matters |
| `ambiguous` | 4 | questions with more than one reasonable reading |
| `unsafe` | 2 | requests to change data, which must be refused |

## Question format

```json
{
  "id": "q012",
  "question": "What is the total payment value of orders that contain at least one ...?",
  "category": "join",
  "difficulty": "hard",
  "expect": "answer",
  "ordered": false,
  "gold_sql": "SELECT ...",
  "check_sql": "SELECT ...",
  "alt_sql": [],
  "skills": ["join_fanout"],
  "notes": "Why the question is in the set.",
  "expected": {"columns": ["payment_value"], "rows": [[1265918.38]]}
}
```

| Field | Meaning |
|---|---|
| `expect` | `answer`: the agent must return the gold result. `clarify_or_answer`: an ambiguous question; a clarifying question is correct, and so is the gold result or one of the `alt_sql` readings. `refuse`: only a refusal is correct (no SQL). |
| `ordered` | Row order matters (ranking questions). |
| `gold_sql` | The reference query. It returns only the columns the question asks for; the agent may return extra columns. |
| `check_sql` | A second query written independently of `gold_sql`; both must return the same data. |
| `alt_sql` | Other valid readings of an ambiguous question. |
| `skills` | Tags used to group failures (e.g. `revenue_rule`, `distinct_orders`, `join_fanout`). |
| `expected` | The verified gold result, stored so changes in the data are noticed. |

All questions follow the shared business definitions in docs/DECISIONS.md (for example, revenue
is the sum of item prices for orders that are not canceled or unavailable, without freight).

## Verifying the questions

```bash
python -m evaluation.verify_questions                   # check every question, exit code 1 on a problem
python -m evaluation.verify_questions --write-expected  # store gold results as expected
```

For each question this runs `gold_sql`, `check_sql` and every `alt_sql` on the database and
checks that the gold and check queries agree and that the gold result still equals `expected`.
It needs the full database (`python -m data.build_db`).

## Running the evaluation

```bash
python -m evaluation.run_eval                            # provider and model from .env, Olist profile
python -m evaluation.run_eval --provider groq            # the other provider configured in .env
python -m evaluation.run_eval --model <name>             # another model of the same provider
python -m evaluation.run_eval --profile no-rules         # Olist without its business rules and examples
python -m evaluation.run_eval --resume evaluation/results/<run>.json   # finish an interrupted run
python -m evaluation.run_eval --compare <run a>.json <run b>.json      # compare runs side by side
python -m evaluation.run_eval --summary evaluation/results/<run>.json
```

`--ids q001,q005`, `--limit N` and `--delay SECONDS` run a subset or slow the run down for
per-minute rate limits. Each question costs about two LLM requests (the SQL and the
explanation). The fallback provider is turned off during a run, so every run measures exactly
one model.

The agent is passed to `run_evaluation(ask, cases)` as a function, so any agent version or
model can be evaluated. A prediction is scored against what the question expects:

| `expect` | Correct when |
|---|---|
| `answer` | the result data matches the gold result |
| `clarify_or_answer` | the agent asks a clarifying question, or the data matches the gold result or an `alt_sql` reading |
| `refuse` | the agent refuses (no data and no clarifying question) |

Every failure gets a type: `sql_error`, `wrong_values`, `wrong_row_count`, `missing_columns`,
`row_order`, `unnecessary_clarification`, `wrongly_refused`, `not_refused` or `agent_error`.
An unavailable LLM provider (`llm_error`, e.g. a used-up quota) or a failing reference query
(`gold_error`) is not the agent's fault: those questions are reported as "not scored", left out
of the accuracy, and asked again with `--resume`.

### Results files

Each run writes `evaluation/results/<date>-<time>_<label>.json` after every question (the
folder is not committed):

- `meta`: label, provider, model, profile, retry limit, a hash of `questions.json` and dates,
- `summary`: accuracy overall and by category, difficulty and skill, failure types, timings,
- `records`: one entry per question with the predicted SQL, outcome, failure type and reason.

`evaluation/metrics.py` computes the summaries and comparisons from these files; the dashboard
uses the same functions.

## Adding a question

1. Write the question, `gold_sql` and an independent `check_sql` (a different query shape, such
   as a subquery instead of a join). Keep results small (at most 50 rows).
2. Run `python -m evaluation.verify_questions --write-expected` and check the stored answer.
3. Run `python -m pytest tests/evaluation`.
