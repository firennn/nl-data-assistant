"""Command-line interface for the NL-to-SQL agent.

Usage:
    python -m agent.cli "Which 5 categories had the highest revenue in 2017?"
    python -m agent.cli                      # interactive mode (type 'exit' to quit)
    python -m agent.cli "..." --chart out.html   # also save the chart as HTML
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent.sql_agent import SQLAgent
from shared.models import QueryResult

HISTORY_TURNS = 6  # (role, text) entries kept for follow-up questions


def format_result(result: QueryResult, show_rows: int = 10) -> str:
    """Human-readable text for a QueryResult."""
    if result.needs_clarification:
        return f"Question for you: {result.clarifying_question}"
    lines = []
    if result.error:
        lines.append(f"Error: {result.error}")
        if result.sql:
            lines += ["", "Last SQL tried:", result.sql]
        return "\n".join(lines)

    df = result.data
    lines += [result.explanation, "", "SQL used:", result.sql or "", ""]
    if df is not None and not df.empty:
        lines.append(df.head(show_rows).to_string(index=False, max_colwidth=50))
        if len(df) > show_rows:
            lines.append(f"... {len(df)} rows in total")
        if df.attrs.get("truncated"):
            lines.append("(result cut at the row limit)")
    else:
        lines.append("(no rows)")
    extra = f"Chart: {result.chart.kind}" if result.chart else ""
    lines += ["", f"{extra} | attempts: {result.attempts}".strip(" |")]
    return "\n".join(lines)


def save_chart(result: QueryResult, path: Path) -> Path | None:
    if not result.ok or result.chart is None:
        return None
    from shared.charts import render_chart

    render_chart(result.data, result.chart).write_html(path, include_plotlyjs="cdn")
    return path


def _history_entry(result: QueryResult) -> tuple[str, str]:
    if result.needs_clarification:
        return ("assistant", result.clarifying_question or "")
    if result.ok:
        return ("assistant", f"Answered with SQL: {result.sql}")
    return ("assistant", f"Could not answer: {result.error}")


def ask_once(agent: SQLAgent, question: str, history: list[tuple[str, str]], show_rows: int):
    result = agent.ask(question, history=history[-HISTORY_TURNS:] or None)
    print(format_result(result, show_rows))
    history += [("user", question), _history_entry(result)]
    return result


def interactive(agent: SQLAgent, show_rows: int) -> int:
    print("Ask a question about the data (type 'exit' to quit).")
    history: list[tuple[str, str]] = []
    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if question.lower() in {"exit", "quit", "q"}:
            return 0
        if question:
            ask_once(agent, question, history, show_rows)


def main(argv: list[str] | None = None, agent: SQLAgent | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ask questions about the Olist database.")
    parser.add_argument("question", nargs="*", help="question in plain language")
    parser.add_argument("--rows", type=int, default=10, help="result rows to print")
    parser.add_argument("--chart", type=Path, help="save the chart to this HTML file")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    agent = agent or SQLAgent()

    if not args.question:
        return interactive(agent, args.rows)

    history: list[tuple[str, str]] = []
    result = ask_once(agent, " ".join(args.question), history, args.rows)
    # Answer clarifying questions when a person is at the keyboard.
    while result.needs_clarification and sys.stdin.isatty():
        reply = input("\n> ").strip()
        if not reply:
            break
        result = ask_once(agent, reply, history, args.rows)

    if args.chart and save_chart(result, args.chart):
        print(f"\nChart saved to {args.chart}")
    if result.needs_clarification:
        return 2
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
