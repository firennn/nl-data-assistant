"""Read-only access to the SQLite database.

Every query from the agent, reports, evaluation and dashboard goes through run_query().
Safety is layered so that a bug in one layer does not allow writes:

1. validate_read_only() rejects anything other than a single SELECT/WITH statement.
2. The connection is opened with mode=ro and PRAGMA query_only=ON.
3. An SQLite authorizer denies every action except reads and function calls.
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from shared.config import get_settings


class UnsafeQueryError(ValueError):
    """The SQL is not a single read-only statement."""


class QueryError(RuntimeError):
    """The database rejected or failed to run the query. The message is safe to show."""


class QueryTimeoutError(QueryError):
    """The query ran longer than the allowed time."""


FORBIDDEN_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE",
    "ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX", "ANALYZE",
)  # fmt: skip

# String literals, quoted identifiers and comments. Literals and identifiers are blanked
# before keyword checks so that e.g. WHERE status = 'drop' is not rejected.
_TOKEN_RE = re.compile(
    r"'(?:[^']|'')*'"  # 'string'
    r'|"(?:[^"]|"")*"'  # "identifier"
    r"|`[^`]*`"  # `identifier`
    r"|\[[^\]]*\]"  # [identifier]
    r"|--[^\n]*"  # -- comment
    r"|/\*.*?(?:\*/|$)",  # /* comment */ (or unterminated)
    re.DOTALL,
)
_FORBIDDEN_RE = re.compile(
    r"\b(" + "|".join(FORBIDDEN_KEYWORDS) + r")\b|\bREPLACE\s+INTO\b", re.IGNORECASE
)
_FIRST_WORD_RE = re.compile(r"^\s*\(*\s*([A-Za-z]+)")


def _mask(sql: str) -> str:
    """Replace literals/identifiers with placeholders and comments with spaces."""

    def repl(match: re.Match[str]) -> str:
        token = match.group(0)
        if token.startswith(("--", "/*")):
            return " "
        return "''"

    return _TOKEN_RE.sub(repl, sql)


def validate_read_only(sql: str) -> str:
    """Return the cleaned SQL if it is one read-only SELECT/WITH statement.

    Raises UnsafeQueryError otherwise.
    """
    if not sql or not sql.strip():
        raise UnsafeQueryError("Empty query.")

    masked = _mask(sql).strip().rstrip(";").strip()
    if not masked:
        raise UnsafeQueryError("Query contains only comments.")
    if ";" in masked:
        raise UnsafeQueryError("Only one statement is allowed.")

    first = _FIRST_WORD_RE.match(masked)
    if not first or first.group(1).upper() not in ("SELECT", "WITH"):
        raise UnsafeQueryError("Only SELECT or WITH queries are allowed.")

    bad = _FORBIDDEN_RE.search(masked)
    if bad:
        raise UnsafeQueryError(f"Forbidden keyword: {bad.group(0).upper()}.")

    return sql.strip().rstrip(";").strip()


_ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    getattr(sqlite3, "SQLITE_FUNCTION", 31),
    getattr(sqlite3, "SQLITE_RECURSIVE", 33),
}


def _authorizer(action: int, *_args: object) -> int:
    return sqlite3.SQLITE_OK if action in _ALLOWED_ACTIONS else sqlite3.SQLITE_DENY


def connect_read_only(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open the database read-only (mode=ro + query_only). Raises FileNotFoundError if missing.

    Used directly only for trusted internal SQL (e.g. schema inspection).
    Anything built from user or model input must go through run_query().
    """
    path = Path(db_path) if db_path else get_settings().db_path
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found at {path}. Build it with: python -m data.build_db"
        )
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON")
    return conn


def run_query(
    sql: str,
    params: Sequence[object] | dict[str, object] | None = None,
    *,
    db_path: str | Path | None = None,
    max_rows: int = 10_000,
    timeout_s: float = 10.0,
) -> pd.DataFrame:
    """Validate and run a read-only query and return the result as a DataFrame.

    If more than max_rows rows are returned, the result is cut to max_rows and
    df.attrs["truncated"] is set to True.

    Raises UnsafeQueryError, QueryTimeoutError or QueryError.
    """
    clean_sql = validate_read_only(sql)
    conn = connect_read_only(db_path)
    deadline = time.monotonic() + timeout_s
    try:
        conn.set_authorizer(_authorizer)
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
        try:
            cursor = conn.execute(clean_sql, params or ())
            columns = [col[0] for col in cursor.description or []]
            rows = cursor.fetchmany(max_rows + 1)
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc).lower():
                raise QueryTimeoutError(f"Query exceeded {timeout_s:g}s time limit.") from exc
            if "not authorized" in str(exc).lower():
                raise UnsafeQueryError("Query attempted a non-read operation.") from exc
            raise QueryError(str(exc)) from exc
        except sqlite3.DatabaseError as exc:
            raise QueryError(str(exc)) from exc
    finally:
        conn.close()

    df = pd.DataFrame(rows[:max_rows], columns=columns)
    df.attrs["truncated"] = len(rows) > max_rows
    return df
