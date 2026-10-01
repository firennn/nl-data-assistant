"""Prompt templates for the NL-to-SQL agent.

The model answers in JSON so the agent can tell SQL answers from clarifying questions:
    {"action": "sql", "sql": "...", "assumptions": "..."}
    {"action": "clarify", "question": "..."}

The prompts are generic. Dataset-specific context (description, business rules, currency,
date range, examples) comes from a DatasetProfile.
"""

from __future__ import annotations

import pandas as pd

from shared.models import DatasetProfile

_MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()

_SQL_SYSTEM_TEMPLATE = """\
You are a senior data analyst. You translate business questions into one SQLite query over the
database described below{about}.

Rules:
- Write exactly one read-only SELECT (or WITH ... SELECT) statement in SQLite syntax.
- Use only tables and columns from the schema. Join on the listed foreign keys.
- Timestamps are text 'YYYY-MM-DD HH:MM:SS': use date(), strftime('%Y-%m', ...) and julianday().
{dataset_rules}\
- {money}Round money and averages to 2 decimals.
- Give result columns short, readable snake_case aliases (e.g. revenue, orders, avg_score).
- For rankings or lists add ORDER BY and a LIMIT (default 10 unless the question says otherwise).
- For trends over time return one row per period, ordered by period.
- {date_rule}

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

_GENERIC_DATE_RULE = """\
"Last month", "recent" and other relative periods are relative to the latest date
  in the data (find it with MAX on the date column), not today's date; state this in the
  assumptions."""

# Generic examples use a made-up table so they fit any database.
EXAMPLES = """\
Examples (for a question -> reply format; they use a made-up table "sales", your schema is the one above):

Question: How many sales were made in 2023?
{"action": "sql", "sql": "SELECT COUNT(*) AS sales FROM sales WHERE strftime('%Y', sale_date) = '2023'", "assumptions": "Counts sales of every status."}

Question: Total amount per month for the last three months
{"action": "sql", "sql": "WITH latest AS (SELECT MAX(sale_date) AS d FROM sales) SELECT strftime('%Y-%m', s.sale_date) AS month, ROUND(SUM(s.amount), 2) AS total_amount FROM sales s, latest WHERE s.sale_date >= date(latest.d, 'start of month', '-2 months') GROUP BY month ORDER BY month", "assumptions": "The last three months are the three latest months in the data."}

Question: How are we doing?
{"action": "clarify", "question": "Which measure and period do you mean, e.g. total amount or number of sales per month?"}

Question: Remove all sales with an amount of 0
{"action": "refuse", "reason": "I can only read data, not change or delete it."}

The examples only show the reply format. Never copy an example reply; answer the actual question.
"""  # noqa: E501

_EXPLAIN_SYSTEM_TEMPLATE = """\
You explain query results to a business user in plain language.
Write 2-4 short sentences: state the key numbers and what they mean, and mention any assumption
or caveat (e.g. what is excluded{data_end}). Use only numbers that appear
in the result. Do not describe the SQL itself and do not use markdown headings or bullet lists.
{money}\
Describe filters exactly as the SQL applies them (e.g. {filter_example}).
"""

_GENERIC_FILTER_EXAMPLE = '"excluding canceled orders", not "completed orders"'


def _month_year(iso_date: str) -> str:
    """'2018-08-21' -> 'Aug 2018' (independent of the system locale)."""
    year, month = iso_date[:4], int(iso_date[5:7])
    return f"{_MONTHS[month - 1]} {year}"


def build_sql_system(profile: DatasetProfile | None = None) -> str:
    """System prompt for SQL generation: generic rules plus the profile's context."""
    about = f", for {profile.description}" if profile and profile.description else ""
    dataset_rules = "".join(f"- {rule}\n" for rule in profile.rules) if profile else ""
    money = f"Money is in {profile.currency}. " if profile and profile.currency else ""
    if profile and profile.date_range:
        start, end = profile.date_range
        date_rule = (
            f'The data is complete from {start} to about {end}. "Last month" or "recent" means\n'
            f"  relative to {end[:7]}, not today's date; state this in the assumptions."
        )
    else:
        date_rule = _GENERIC_DATE_RULE
    # str.replace instead of str.format: the template contains JSON braces.
    return (
        _SQL_SYSTEM_TEMPLATE.replace("{about}", about)
        .replace("{dataset_rules}", dataset_rules)
        .replace("{money}", money)
        .replace("{date_rule}", date_rule)
    )


def build_explain_system(profile: DatasetProfile | None = None) -> str:
    """System prompt for the explanation: generic rules plus currency and data end date."""
    data_end = ""
    if profile and profile.date_range:
        data_end = f", or that the data ends in {_month_year(profile.date_range[1])}"
    money = ""
    if profile and profile.currency:
        money = f"Money is in {profile.currency}"
        if profile.currency_format:
            money += f": write amounts like {profile.currency_format}"
            if not profile.currency_format.startswith("$"):
                money += ", never with a plain $ sign"
        money += ".\n"
    filter_example = (profile.filter_example if profile else None) or _GENERIC_FILTER_EXAMPLE
    return (
        _EXPLAIN_SYSTEM_TEMPLATE.replace("{data_end}", data_end)
        .replace("{money}", money)
        .replace("{filter_example}", filter_example)
    )


# Generic versions, used when no profile is given.
SQL_SYSTEM = build_sql_system()
EXPLAIN_SYSTEM = build_explain_system()


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
    examples: str | None = None,
) -> str:
    """Prompt for SQL generation. failed_attempts holds (sql_or_reply, error) pairs from earlier
    tries so the model can correct itself. examples defaults to the generic EXAMPLES."""
    parts = [f"Database schema:\n{schema_text}", examples or EXAMPLES, format_history(history)]
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
