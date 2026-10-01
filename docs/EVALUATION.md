# Evaluation of the NL-to-SQL agent

How accurate is the agent on business questions it has never seen, and where does it fail?
This page describes the method, the results of the evaluation runs and their limitations.
Code and question format: [evaluation/README.md](../evaluation/README.md).

## Method

**Held-out questions.** 27 business questions over the Olist database
(`evaluation/questions.json`), written for this evaluation and never used as prompt examples
or training data; a test fails if a question or its SQL appears in the agent prompts or
dataset profiles.

| Category | Questions | Examples of what is tested |
|---|---|---|
| filter | 4 | single-table conditions, NULL handling |
| aggregation | 4 | averages, percentages, the revenue rule, counting orders once |
| join | 5 | 2-4 tables, distinct customers, join fan-out |
| time | 5 | grouping by month or day, date arithmetic, comparing periods |
| ranking | 3 | top-N lists where the order matters |
| ambiguous | 4 | questions with more than one reasonable reading |
| unsafe | 2 | requests to change data |

**Verified answers.** Every reference ("gold") query has a second query written independently
(for example with subqueries instead of joins); both must return the same data, and the
verified result is stored with the question. `python -m evaluation.verify_questions` re-checks
all of them against the database.

**Scoring by result data.** A prediction is correct when the data it returns matches the
reference result, whatever SQL produced it: column names are ignored, extra columns are
allowed, row order only counts for ranking questions, and numbers match within 0.005 or 0.01%
(the agent rounds money to 2 decimals).

| Expected behaviour | Counted as correct when |
|---|---|
| answer | the result data matches the reference |
| clarify or answer (ambiguous) | the agent asks a clarifying question, or its data matches one of the valid readings |
| refuse (unsafe) | the agent refuses and returns no data |

**Failure types.** Every wrong answer gets one type: wrong values, wrong row count, missing
columns, row order, SQL error, unnecessary clarification, wrongly refused or not refused.
Questions also carry skill tags (for example `revenue_rule`, `join_fanout`,
`distinct_orders`), so failures can be grouped by what the question tests. An unavailable LLM
provider (quota, server error) is not the agent's fault: such questions are "not scored" and
asked again with `--resume`.

**Setups compared.** Each run uses one provider and model (the fallback provider is turned
off), with either the normal Olist profile or a "no rules" profile that removes the business
rules and examples, to measure what they add.

## Results

Five runs on 2 October 2026, 27 questions each, no question left unscored.

| Setup | Runs | Accuracy | Avg time per question |
|---|---|---|---|
| gemini-3.1-flash-lite, Olist profile | 3 | **96%** in each run (26/27) | 7.5-8.3 s |
| gemini-3.1-flash-lite, without business rules | 1 | 93% (25/27) | 8.7 s |
| openai/gpt-oss-120b (Groq), Olist profile | 1 | 85% (23/27) | 15.9 s |

### By category

| Category | Gemini, Olist (each of 3 runs) | Gemini, no rules | Groq, Olist |
|---|---|---|---|
| filter | 100% | 100% | 100% |
| aggregation | 100% | 75% | 100% |
| join | 80% | 80% | 60% |
| time | 100% | 100% | 60% |
| ranking | 100% | 100% | 100% |
| ambiguous | 100% | 100% | 100% |
| unsafe | 100% | 100% | 100% |

### Questions that failed

| Question | Gemini, Olist (3 runs) | Gemini, no rules | Groq, Olist | What happened |
|---|---|---|---|---|
| q009 average order value (item prices, no freight) | correct | wrong | correct | Without the rules, canceled and unavailable orders were not excluded. |
| q012 payment value of orders with a bed_bath_table product | correct | wrong | wrong | No rules: payments were joined to items, so each payment was counted once per item (1.71 million instead of 1.27 million BRL). Groq: added the canceled-order filter, which the question does not ask for. |
| q013 orders with an item from a seller in another state than the customer | **wrong in all 3 runs** | correct | correct | With the rules, Gemini added `status NOT IN ('canceled', 'unavailable')` every time: the revenue rule applied to a question about orders. |
| q015 freight of delivered orders for Sao Paulo city customers | correct | correct | wrong | "Delivered" taken as "has a delivery date" instead of status `delivered`; one canceled order has a delivery date (30.42 BRL difference). |
| q016 orders delivered per month in 2018 | correct | correct | wrong | Same reading as q015: one order more in March 2018. |
| q019 revenue Q1 2017 vs Q1 2018 | correct | correct | wrong | **Numbers exactly right**; the rows were labelled "2017-Q1" instead of "2017", so the label column does not match. Kept as a failure (the question set is not changed after seeing results). |

### Findings

1. **The business rules help, and they are over-applied.** Without them the agent makes the
   classic mistakes: including canceled orders in an average order value (q009) and
   double-counting through a join (q012). With them, both models added the revenue rule's
   status filter to questions that are not about revenue (Gemini q013 in all three runs, Groq
   q012). The rule should be reworded to apply only to revenue questions, not removed.
2. **Stable results.** The three Gemini runs with the same setup scored the same and failed on
   the same question for the same reason, so this failure is systematic, not noise.
3. **No clarifying questions.** On the 4 ambiguous questions no model asked for clarification;
   each picked a reasonable reading, as the profile tells it to. The readings were consistent:
   "orders delivered in 2017" was always counted by delivery date, and Gemini with the rules
   and Groq always compared late deliveries by timestamp rather than by date.
4. **Safety held.** Every request to change data was refused, in every run.
5. **Joins and dates are where models differ.** All setups were perfect on filters, rankings,
   ambiguous and unsafe questions; the failures are in joins (fan-out, extra filters) and in
   how "delivered" is defined.

## Limitations

- **Small question set.** With 27 questions one question is about 3.7 percentage points, so
  differences of one or two questions between setups are not significant. The failure types
  and the questions that fail are more informative than the exact percentage.
- **Run-to-run variation.** Model answers vary between runs even with the same setup;
  repeated runs are reported where available.
- **One database.** All questions are about the Olist data; accuracy on uploaded data with
  generic profiles is not measured yet.
- **Free-tier models only**, chosen to keep the project free to run.
- **Strict labels.** A result with the right numbers but differently formatted labels (for
  example "2017-Q1" instead of "2017") counts as wrong; such near-misses are listed below.

## Reproducing

```bash
python -m evaluation.verify_questions                    # check the reference answers
python -m evaluation.run_eval --label gemini-olist       # provider and model from .env
python -m evaluation.run_eval --profile no-rules         # without the business rules
python -m evaluation.run_eval --provider groq            # the second configured provider
python -m evaluation.run_eval --compare evaluation/results/<a>.json evaluation/results/<b>.json
```

Each run costs about two LLM requests per question. Results are saved after every question in
`evaluation/results/` (not committed) and can be browsed on the dashboard's Evaluation page.
