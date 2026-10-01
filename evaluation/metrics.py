"""Accuracy and failure summaries for evaluation results (owner: owner3).

Works on the flat records stored in a results file (see run_eval.EvalRecord.to_dict), so the
command line and the dashboard use the same numbers.
"""

from __future__ import annotations

from collections import Counter


def _group(records: list[dict], key: str, order: tuple[str, ...] = ()) -> dict[str, dict]:
    """Scored/correct/accuracy per value of `key` (a list-valued key counts once per value)."""
    groups: dict[str, list[dict]] = {}
    for r in records:
        values = r.get(key) or []
        for value in values if isinstance(values, list) else [values]:
            groups.setdefault(value, []).append(r)

    def rank(name: str) -> tuple[int, str]:
        return (order.index(name), name) if name in order else (len(order), name)

    result = {}
    for name in sorted(groups, key=rank):
        scored = [r for r in groups[name] if r["scored"]]
        correct = sum(r["correct"] for r in scored)
        result[name] = {
            "scored": len(scored),
            "correct": correct,
            "failed": len(scored) - correct,
            "accuracy": correct / len(scored) if scored else None,
        }
    return result


def summarize(records: list[dict]) -> dict:
    """Overall accuracy, accuracy by category/difficulty/skill, failure types and timings.

    Questions that were not scored (LLM outage or a broken reference query) are counted
    separately and left out of every accuracy.
    """
    from evaluation.run_eval import CATEGORIES, DIFFICULTIES

    scored = [r for r in records if r["scored"]]
    correct = sum(r["correct"] for r in scored)
    timed = [r["latency_s"] for r in records if r.get("latency_s")]
    tried = [r["attempts"] for r in records if r.get("attempts")]
    by_skill = _group(records, "skills")
    return {
        "questions": len(records),
        "scored": len(scored),
        "not_scored": len(records) - len(scored),
        "correct": correct,
        "accuracy": correct / len(scored) if scored else None,
        "by_category": _group(records, "category", CATEGORIES),
        "by_difficulty": _group(records, "difficulty", DIFFICULTIES),
        # Skills with the most failures first: these are the patterns worth fixing.
        "by_skill": dict(sorted(by_skill.items(), key=lambda kv: (-kv[1]["failed"], kv[0]))),
        "failure_types": dict(Counter(r["failure_type"] for r in records if r["failure_type"])),
        "outcomes": dict(Counter(r["outcome"] for r in records)),
        "clarified": sum(r.get("matched") == "clarification" for r in records),
        "alt_reading": sum(str(r.get("matched", "")).startswith("alt_sql") for r in records),
        "extra_columns": sum(r["correct"] and r.get("extra_columns", 0) > 0 for r in records),
        "avg_latency_s": round(sum(timed) / len(timed), 2) if timed else None,
        "avg_attempts": round(sum(tried) / len(tried), 2) if tried else None,
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def format_summary(data: dict) -> str:
    """Plain-text summary of one results file."""
    meta, s = data.get("meta", {}), summarize(data.get("records", []))
    lines = [
        f"Run: {meta.get('label', '?')}  (provider {meta.get('provider', '?')}, "
        f"model {meta.get('model', '?')}, profile {meta.get('profile', '?')})",
        f"Accuracy: {_pct(s['accuracy'])}  ({s['correct']}/{s['scored']} scored, "
        f"{s['not_scored']} not scored)",
        "",
        "By category:",
    ]
    for name, g in s["by_category"].items():
        lines.append(f"  {name:13} {_pct(g['accuracy']):>5}  ({g['correct']}/{g['scored']})")
    lines += ["", "By difficulty:"]
    for name, g in s["by_difficulty"].items():
        lines.append(f"  {name:13} {_pct(g['accuracy']):>5}  ({g['correct']}/{g['scored']})")
    if s["failure_types"]:
        lines += ["", "Failure types:"]
        for name, count in sorted(s["failure_types"].items(), key=lambda kv: -kv[1]):
            lines.append(f"  {name:27} {count}")
    failing = {k: v for k, v in s["by_skill"].items() if v["failed"]}
    if failing:
        lines += ["", "Skills in failed questions:"]
        for name, g in failing.items():
            lines.append(f"  {name:20} {g['failed']} failed of {g['scored']}")
    lines += [
        "",
        f"Clarifying questions on ambiguous questions: {s['clarified']}; "
        f"answers using another valid reading: {s['alt_reading']}",
        f"Average time per question: {s['avg_latency_s']} s; average attempts: {s['avg_attempts']}",
    ]
    return "\n".join(lines)


def compare_runs(runs: list[dict]) -> dict:
    """Side-by-side numbers for several results files (for the CLI and the dashboard)."""
    labels = [r.get("meta", {}).get("label", f"run {i + 1}") for i, r in enumerate(runs)]
    summaries = [summarize(r.get("records", [])) for r in runs]

    rows = [{"metric": "accuracy", "values": [s["accuracy"] for s in summaries]}]
    categories = list(dict.fromkeys(c for s in summaries for c in s["by_category"]))
    for name in categories:
        values = [s["by_category"].get(name, {}).get("accuracy") for s in summaries]
        rows.append({"metric": f"category: {name}", "values": values})

    by_id: dict[str, dict] = {}
    for i, run in enumerate(runs):
        for r in run.get("records", []):
            item = by_id.setdefault(
                r["id"], {"id": r["id"], "question": r["question"], "results": [None] * len(runs)}
            )
            item["results"][i] = r["correct"] if r["scored"] else None
    questions = [by_id[k] for k in sorted(by_id)]
    return {"labels": labels, "rows": rows, "questions": questions}


def format_comparison(runs: list[dict]) -> str:
    """Plain-text comparison of several results files."""
    table = compare_runs(runs)
    width = max(12, *(len(label) for label in table["labels"]))
    header = f"{'':22}" + "".join(f"{label:>{width + 2}}" for label in table["labels"])
    lines = [header]
    for row in table["rows"]:
        values = "".join(f"{_pct(v):>{width + 2}}" for v in row["values"])
        lines.append(f"{row['metric']:22}{values}")

    differing = [q for q in table["questions"] if len(set(q["results"])) > 1]
    if differing:
        lines += [
            "",
            "Questions where the runs differ (ok = correct, XX = wrong, -- = not scored):",
        ]
        for q in differing:
            marks = " ".join("--" if v is None else ("ok" if v else "XX") for v in q["results"])
            lines.append(f"  {q['id']}  {marks}  {q['question']}")
    return "\n".join(lines)
