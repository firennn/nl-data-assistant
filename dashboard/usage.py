"""Local usage log for the dashboard: one JSON line per question asked in the chat.

The log stays on this computer (dashboard/usage_log.jsonl is gitignored). For uploaded
databases the question text is not stored, only the outcome, so no uploaded data ends up in
the log.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from shared.models import QueryResult


def outcome_of(result: QueryResult) -> str:
    if result.needs_clarification:
        return "clarification"
    if result.ok:
        return "answered"
    return "error"


def log_question(
    path: Path,
    *,
    result: QueryResult,
    source_key: str,
    source_kind: str,
    latency_s: float,
    now: datetime | None = None,
) -> dict:
    """Append one entry to the usage log and return it."""
    entry = {
        "time": (now or datetime.now()).isoformat(timespec="seconds"),
        "source": source_key,
        "source_kind": source_kind,
        "question": result.question if source_kind == "demo" else None,
        "outcome": outcome_of(result),
        "chart": result.chart.kind if result.chart else None,
        "rows": None if result.data is None else len(result.data),
        "attempts": result.attempts,
        "latency_s": round(latency_s, 2),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def read_log(path: Path) -> pd.DataFrame:
    """The usage log as a DataFrame (empty if there is no log yet). Broken lines are skipped."""
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    df = pd.DataFrame(rows)
    if not df.empty:
        df["time"] = pd.to_datetime(df["time"], errors="coerce")
    return df


def usage_summary(df: pd.DataFrame) -> dict:
    """Headline numbers for the usage page."""
    if df.empty:
        return {"questions": 0, "answered_pct": None, "avg_latency_s": None, "days": 0}
    return {
        "questions": len(df),
        "answered_pct": float((df["outcome"] == "answered").mean()),
        "avg_latency_s": round(float(df["latency_s"].mean()), 2),
        "days": int(df["time"].dt.date.nunique()),
    }
