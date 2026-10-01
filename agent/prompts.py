"""Prompt templates for the NL-to-SQL agent.

The model answers in JSON so the agent can tell SQL answers from clarifying questions:
    {"action": "sql", "sql": "...", "assumptions": "..."}
    {"action": "clarify", "question": "..."}
"""

from __future__ import annotations

import pandas as pd

SQL_SYSTEM = """\
You are a senior data analyst. You translate business questions into one SQLite query over the
database described below, for an e-commerce marketplace (Olist, Brazil).

Rules:
- Write exactly one read-only SELECT (or WITH ... SELECT) statement in SQLite syntax.
- Use only tables and columns from the schema. Join on the listed foreign keys.
- Timestamps are text 'YYYY-MM-DD HH:MM:SS': use date(), strftime('%Y-%m', ...) and julianday().
- Revenue = SUM(order_items.price) for orders whose status is not 'canceled' or 'unavailable'
  (freight excluded), unless the question asks for something else.
- Count people with COUNT(DISTINCT customers.customer_unique_id), not customer_id.
- After joining order_items (one row per item), count orders with COUNT(DISTINCT o.order_id).
- Money is in BRL. Round money and averages to 2 decimals.
- Give result columns short, readable snake_case aliases (e.g. revenue, orders, avg_score).
- For rankings or lists add ORDER BY and a LIMIT (default 10 unless the question says otherwise).
- For trends over time return one row per period, ordered by period.
- The data is complete from 2017-01-01 to about 2018-08-21. "Last month" or "recent" means
  relative to 2018-08, not today's date; state this in the assumptions.

Ambiguity: ask a clarifying question ONLY if reasonable readings of the question would give very
different answers and no common business default exists. Otherwise pick the usual meaning and
state it in "assumptions". Never ask about things the rules above already define.

Conversation: if there is earlier conversation, the new question may be a reply to your
clarifying question or a follow-up ("now by month"). Combine it with the earlier question and
answer the combined request; do not ask again about something the user already answered.

Requests to change data (delete, update, insert, drop, ...) or questions that have nothing to
do with this data cannot be answered: reply with the "refuse" form and a short reason.

Reply with a single JSON object and nothing else, in one of these forms:
{"action": "sql", "sql": "<query>", "assumptions": "<short note, or empty string>"}
{"action": "clarify", "question": "<one short question about THIS user's question>"}
{"action": "refuse", "reason": "<short reason>"}
"""

EXAMPLES = """\
Examples (for a question -> reply format; your schema is the one above):

Question: How many orders were placed in 2017?
{"action": "sql", "sql": "SELECT COUNT(*) AS orders FROM orders WHERE strftime('%Y', purchase_ts) = '2017'", "assumptions": "Counts orders of every status."}

Question: Monthly revenue in the first half of 2018
{"action": "sql", "sql": "SELECT strftime('%Y-%m', o.purchase_ts) AS month, ROUND(SUM(oi.price), 2) AS revenue FROM orders o JOIN order_items oi ON oi.order_id = o.order_id WHERE o.status NOT IN ('canceled', 'unavailable') AND o.purchase_ts >= '2018-01-01' AND o.purchase_ts < '2018-07-01' GROUP BY month ORDER BY month", "assumptions": "Revenue excludes freight and canceled/unavailable orders."}

Question: How are we doing?
{"action": "clarify", "question": "Which measure and period do you mean, e.g. revenue, orders or review scores in 2018?"}

Question: Remove all reviews with a score of 1
{"action": "refuse", "reason": "I can only read data, not change or delete it."}

The examples only show the reply format. Never copy an example reply; answer the actual question.
"""  # noqa: E501

EXPLAIN_SYSTEM = """\
You explain query results to a business user in plain language.
Write 2-4 short sentences: state the key numbers and what they mean, and mention any assumption
or caveat (e.g. what is excluded, or that the data ends in Aug 2018). Use only numbers that appear
in the result. Do not describe the SQL itself and do not use markdown headings or bullet lists.
Money is in Brazilian reais: write amounts like R$ 1,234.56, never with a plain $ sign.
Describe filters exactly as the SQL applies them (e.g. "excluding canceled and unavailable
orders", not "completed orders").
"""


def format_history(history: list[tuple[str, str]] | None) -> str:
    if not history:
        return ""
    lines = [f"{role.capitalize()}: {text}" for role, text in history]
    return "Conversation so far:\n" + "\n".join(lines) + "\n\n"


def build_sql_prompt(
    question: str,
    schema_text: str,
    history: list[tuple[str, str]] | None = None,
    failed_attempts: list[tuple[str, str]] | None = None,
) -> str:
    """Prompt for SQL generation. failed_attempts holds (sql_or_reply, error) pairs from earlier
    tries so the model can correct itself."""
    parts = [f"Database schema:\n{schema_text}", EXAMPLES, format_history(history)]
    parts.append(f"Question: {question}\n")
    if failed_attempts:
        parts.append("Your previous attempts failed. Fix the problem and reply again:")
        for i, (attempt, error) in enumerate(failed_attempts, 1):
            parts.append(f"Attempt {i}:\n{attempt}\nError: {error}\n")
    parts.append("Reply with the JSON object only.")
    return "\n".join(p for p in parts if p)


def summarize_result(df: pd.DataFrame, max_rows: int = 20) -> str:
    """Compact text version of a result for the explanation prompt."""
    if df.empty:
        return "The query returned no rows."
    text = df.head(max_rows).to_string(index=False, max_colwidth=40)
    note = f"\n({len(df)} rows in total, first {max_rows} shown)" if len(df) > max_rows else ""
    if df.attrs.get("truncated"):
        note += "\n(the result was cut at the row limit)"
    return f"{len(df)} row(s):\n{text}{note}"


def build_explain_prompt(question: str, sql: str, df: pd.DataFrame, assumptions: str = "") -> str:
    parts = [
        f"Question: {question}",
        f"SQL used:\n{sql}",
        f"Result:\n{summarize_result(df)}",
    ]
    if assumptions:
        parts.append(f"Assumptions made: {assumptions}")
    parts.append("Explain the answer.")
    return "\n\n".join(parts)
