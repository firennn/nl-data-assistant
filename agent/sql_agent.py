"""NL-to-SQL agent: question -> schema-aware prompt -> SQL -> safety check -> read-only run
-> chart selection -> plain-language explanation.

The public interface below is part of the shared contract (the dashboard and evaluation
call SQLAgent.ask). The implementation is built in the agent phase.
"""

from __future__ import annotations

from shared.config import Settings
from shared.llm import LLMProvider
from shared.models import QueryResult


class SQLAgent:
    """Answers business questions about the database in plain language."""

    def __init__(self, llm: LLMProvider | None = None, settings: Settings | None = None) -> None:
        """
        Args:
            llm: LLM to use; defaults to shared.llm.get_llm().
            settings: project settings; defaults to shared.config.get_settings().
        """
        self._llm = llm
        self._settings = settings

    def ask(self, question: str, history: list[tuple[str, str]] | None = None) -> QueryResult:
        """Answer `question` and return a QueryResult.

        Args:
            question: the user's question in plain language.
            history: earlier (role, text) turns, e.g. a clarifying question and the user's
                reply, so follow-up answers are understood in context.

        Returns a QueryResult that is either ok (sql + data + chart + explanation),
        needs_clarification (with clarifying_question), or has an error message.
        Never raises for bad SQL or unsafe queries; those are reported in the result.
        """
        raise NotImplementedError("SQLAgent.ask is implemented in the agent phase.")
