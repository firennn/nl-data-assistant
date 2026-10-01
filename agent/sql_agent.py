"""NL-to-SQL agent: question -> schema-aware prompt -> SQL -> safety check -> read-only run
-> chart selection -> plain-language explanation.

The public interface (SQLAgent.ask) is part of the shared contract: the dashboard and the
evaluation call it and only rely on the returned QueryResult.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pandas as pd

from agent.chart_selector import choose_chart
from agent.prompts import (
    build_explain_prompt,
    build_explain_system,
    build_sql_prompt,
    build_sql_system,
)
from shared.config import Settings, get_settings
from shared.db import QueryError, QueryTimeoutError, UnsafeQueryError, run_query, validate_read_only
from shared.llm import LLMError, LLMProvider, get_llm
from shared.models import DatasetProfile, QueryResult
from shared.profiles import OLIST_PROFILE
from shared.schema import SCHEMA_MAX_CHARS, describe_schema, schema_to_prompt

logger = logging.getLogger(__name__)

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


class ReplyFormatError(ValueError):
    """The model's reply could not be understood."""


def parse_reply(text: str) -> dict[str, str]:
    """Parse the model's JSON reply, tolerating code fences or text around the object."""
    match = _JSON_OBJECT_RE.search(text or "")
    if not match:
        raise ReplyFormatError("Reply did not contain a JSON object.")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ReplyFormatError(f"Reply was not valid JSON: {exc.msg}.") from exc
    if not isinstance(data, dict):
        raise ReplyFormatError("Reply JSON must be an object.")

    action = str(data.get("action", "")).strip().lower()
    if action == "clarify":
        question = str(data.get("question") or "").strip()
        if not question:
            raise ReplyFormatError("Clarify reply is missing 'question'.")
        return {"action": "clarify", "question": question}
    if action == "refuse":
        reason = str(data.get("reason") or "").strip() or "This request cannot be answered."
        return {"action": "refuse", "reason": reason}
    if action == "sql" or (not action and data.get("sql")):
        sql = str(data.get("sql") or "").strip()
        if not sql:
            raise ReplyFormatError("SQL reply is missing 'sql'.")
        return {"action": "sql", "sql": sql, "assumptions": str(data.get("assumptions") or "")}
    raise ReplyFormatError("Reply 'action' must be 'sql', 'clarify' or 'refuse'.")


class SQLAgent:
    """Answers business questions about the database in plain language."""

    def __init__(
        self,
        llm: LLMProvider | None = None,
        settings: Settings | None = None,
        *,
        db_path: str | Path | None = None,
        max_rows: int = 1000,
        profile: DatasetProfile | None = None,
    ) -> None:
        """
        Args:
            llm: LLM to use; defaults to shared.llm.get_llm().
            settings: project settings; defaults to shared.config.get_settings().
            db_path: database to query; defaults to settings.db_path.
            max_rows: maximum rows returned per query (the result is marked as truncated).
            profile: business rules and context for the database; defaults to the Olist
                profile. Pass a profile for any other database, e.g. an uploaded one.
        """
        self._settings = settings or get_settings()
        self._llm = llm
        self._db_path = Path(db_path) if db_path else self._settings.db_path
        self._max_rows = max_rows
        self._profile = profile or OLIST_PROFILE
        self._sql_system = build_sql_system(self._profile)
        self._explain_system = build_explain_system(self._profile)
        self._schema_text: str | None = None

    @property
    def llm(self) -> LLMProvider:
        if self._llm is None:
            self._llm = get_llm(self._settings)
        return self._llm

    @property
    def max_attempts(self) -> int:
        return 1 + max(0, self._settings.agent_max_retries)

    @property
    def profile(self) -> DatasetProfile:
        return self._profile

    def schema_text(self) -> str:
        """Schema description used in prompts (computed once per agent).

        Sample values are only read and included if the profile allows it. The text is kept
        within SCHEMA_MAX_CHARS. Raises FileNotFoundError or QueryError (incl. timeouts).
        """
        if self._schema_text is None:
            samples = self._profile.include_samples
            schema = describe_schema(self._db_path, sample_values=3 if samples else 0)
            self._schema_text = schema_to_prompt(
                schema, include_samples=samples, max_chars=SCHEMA_MAX_CHARS
            )
        return self._schema_text

    def ask(self, question: str, history: list[tuple[str, str]] | None = None) -> QueryResult:
        """Answer `question` and return a QueryResult.

        Args:
            question: the user's question in plain language.
            history: earlier (role, text) turns, e.g. a clarifying question and the user's
                reply, so follow-up answers are understood in context.

        Returns a QueryResult that is either ok (sql + data + chart + explanation),
        needs_clarification (with clarifying_question), or has an error message.
        Never raises for bad SQL, unsafe queries or an unreadable database; those are
        reported in the result.
        """
        question = (question or "").strip()
        if not question:
            return QueryResult(question=question, error="Please ask a question.")

        try:
            schema_text = self.schema_text()
        except (FileNotFoundError, QueryError) as exc:
            return QueryResult(question=question, error=str(exc))

        failed: list[tuple[str, str]] = []
        last_sql: str | None = None
        for attempt in range(1, self.max_attempts + 1):
            prompt = build_sql_prompt(
                question, schema_text, history, failed, examples=self._profile.examples
            )
            try:
                reply_text = self.llm.complete(prompt, system=self._sql_system, json_mode=True).text
            except LLMError as exc:
                return QueryResult(
                    question=question,
                    sql=last_sql,
                    error=f"The language model is not available: {exc}",
                    attempts=attempt,
                )

            try:
                reply = parse_reply(reply_text)
            except ReplyFormatError as exc:
                failed.append((reply_text.strip()[:500], str(exc)))
                continue

            if reply["action"] == "refuse":
                return QueryResult(
                    question=question,
                    error=f"Cannot answer: {reply['reason']}",
                    attempts=attempt,
                )

            if reply["action"] == "clarify":
                if attempt == 1:
                    return QueryResult(
                        question=question,
                        needs_clarification=True,
                        clarifying_question=reply["question"],
                        attempts=attempt,
                    )
                failed.append((reply_text.strip()[:500], "Do not ask again; write the SQL."))
                continue

            sql = reply["sql"]
            last_sql = sql
            try:
                validate_read_only(sql)
            except UnsafeQueryError as exc:
                # Never retried: a request to change data should not be rephrased until it runs.
                return QueryResult(
                    question=question,
                    sql=sql,
                    error=f"Blocked unsafe query ({exc}) This assistant can only read data.",
                    attempts=attempt,
                )

            try:
                df = run_query(sql, db_path=self._db_path, max_rows=self._max_rows)
            except QueryTimeoutError as exc:
                failed.append((sql, f"{exc} Write a simpler or more selective query."))
                continue
            except QueryError as exc:
                failed.append((sql, str(exc)))
                continue

            return QueryResult(
                question=question,
                sql=sql,
                data=df,
                chart=choose_chart(df, title=question),
                explanation=self._explain(question, sql, df, reply["assumptions"]),
                attempts=attempt,
            )

        last_error = failed[-1][1] if failed else "unknown error"
        return QueryResult(
            question=question,
            sql=last_sql,
            error=f"Could not answer after {self.max_attempts} attempts. Last error: {last_error}",
            attempts=self.max_attempts,
        )

    def _explain(self, question: str, sql: str, df: pd.DataFrame, assumptions: str) -> str:
        """Plain-language explanation; falls back to a short summary if the LLM call fails."""
        try:
            text = self.llm.complete(
                build_explain_prompt(question, sql, df, assumptions),
                system=self._explain_system,
                temperature=0.2,
                max_tokens=400,
            ).text.strip()
            if text:
                return text
        except LLMError as exc:
            logger.warning("Explanation failed: %s", exc)
        summary = f"The query returned {len(df)} row(s)."
        return f"{summary} Assumptions: {assumptions}" if assumptions else summary
