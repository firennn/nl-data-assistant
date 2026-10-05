import os
import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from dashboard import app
from dashboard.context import available_sources, get_context, remove_source
from dashboard.views.chat import CHAT_KEY
from dashboard.views.upload import (
    INFO_KEY,
    UPLOAD_KEY,
    discard_upload,
    load_upload,
    remove_stale_uploads,
    session_folder,
)
from data.upload import UploadError
from shared.models import QueryResult

APP = str(Path(app.__file__).resolve())


def write_csv(folder: Path, name: str, text: str) -> Path:
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def sales_csv(tmp_path) -> Path:
    return write_csv(
        tmp_path,
        "Sales Export.csv",
        "Order ID,Order Date,Amount\nA1,2024-01-05,10.5\nA2,2024-01-06,20\n",
    )


def new_state(tmp_path) -> dict:
    return {"uploads_dir": str(tmp_path / "uploads")}


# ---- loading and replacing


def test_upload_becomes_the_current_source(tmp_path, sales_csv):
    state = new_state(tmp_path)
    up = load_upload(get_context(state), [sales_csv])
    ctx = get_context(state)
    assert ctx.source.key == UPLOAD_KEY and ctx.source.kind == "upload"
    assert ctx.source.db_path == up.db_path and up.db_path.exists()
    assert ctx.source.profile is up.profile
    assert up.db_path.parent == session_folder(ctx)
    assert ctx.demo.key == "olist"
    info = state[INFO_KEY]
    assert info["files"] == ["Sales Export.csv"]
    assert info["tables"] == {"sales_export": (2, 3)}
    assert any("sales_export" in note for note in info["notes"])


def test_new_upload_replaces_the_old_one(tmp_path, sales_csv):
    state = new_state(tmp_path)
    first = load_upload(get_context(state), [sales_csv])
    state[CHAT_KEY] = {UPLOAD_KEY: [{"role": "user", "text": "old question"}], "olist": []}
    other = write_csv(tmp_path, "customers.csv", "id,city\n1,Lyon\n")
    second = load_upload(get_context(state), [other])
    assert not first.db_path.exists() and second.db_path.exists()
    assert list(available_sources(state, get_context(state).settings)) == ["olist", UPLOAD_KEY]
    assert get_context(state).source.db_path == second.db_path
    assert UPLOAD_KEY not in state[CHAT_KEY] and "olist" in state[CHAT_KEY]


def test_failed_upload_keeps_the_earlier_one(tmp_path, sales_csv):
    state = new_state(tmp_path)
    first = load_upload(get_context(state), [sales_csv])
    bad = write_csv(tmp_path, "report.xlsx", "not a csv")
    with pytest.raises(UploadError, match="not a CSV or SQLite file"):
        load_upload(get_context(state), [bad])
    assert get_context(state).source.db_path == first.db_path and first.db_path.exists()


def test_discard_upload(tmp_path, sales_csv):
    state = new_state(tmp_path)
    up = load_upload(get_context(state), [sales_csv])
    discard_upload(get_context(state))
    ctx = get_context(state)
    assert ctx.source.key == "olist"
    assert UPLOAD_KEY not in available_sources(state, ctx.settings)
    assert INFO_KEY not in state and not up.db_path.exists()
    discard_upload(ctx)  # nothing left to remove


def test_demo_source_cannot_be_removed():
    state: dict = {}
    with pytest.raises(ValueError):
        remove_source(state, "olist", get_context(state).settings)


# ---- cleanup of old session folders


def test_remove_stale_uploads(tmp_path):
    root = tmp_path / "uploads"
    old, recent, mine = root / "old", root / "recent", root / "mine"
    for folder in (old, recent, mine):
        folder.mkdir(parents=True)
        (folder / "x.db").write_bytes(b"")
    now = time.time()
    for folder in (old, mine):
        os.utime(folder, (now - 2 * 86400, now - 2 * 86400))
    removed = remove_stale_uploads(root, keep=mine, now=now)
    assert removed == [old]
    assert not old.exists() and recent.exists() and mine.exists()
    assert remove_stale_uploads(tmp_path / "missing") == []


# ---- the page


class FakeAgent:
    def __init__(self):
        self.calls = []

    def ask(self, question, history=None):
        self.calls.append(question)
        return QueryResult(question=question, error="Cannot answer: test.")


def make_app(tmp_path, state: dict | None = None, factory=None) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    for key, value in {**new_state(tmp_path), **(state or {})}.items():
        at.session_state[key] = value
    at.session_state["usage_log"] = str(tmp_path / "usage.jsonl")
    if factory is not None:
        at.session_state["agent_factory"] = factory
    return at


def go_to(at: AppTest, title: str) -> AppTest:
    radio = at.sidebar.radio[0]
    label = next(option for option in radio.options if option.endswith(title))
    return radio.set_value(label).run()


def test_upload_page_without_data(tmp_path):
    at = go_to(make_app(tmp_path).run(), "Upload data")
    assert not at.exception
    assert "LLM provider API" in at.info[0].value
    assert at.button[0].label == "Load data" and at.button[0].disabled
    assert not at.success


def test_upload_page_shows_the_loaded_data_and_chat_uses_it(tmp_path, sales_csv):
    state = new_state(tmp_path)
    up = load_upload(get_context(state), [sales_csv])
    asked = []

    def factory(source, settings):
        asked.append(source.db_path)
        return FakeAgent()

    at = go_to(make_app(tmp_path, state, factory).run(), "Upload data")
    assert not at.exception
    assert at.sidebar.selectbox[0].value == UPLOAD_KEY
    assert "Sales Export.csv" in at.success[0].value
    assert at.dataframe[0].value.to_dict("records") == [
        {"table": "sales_export", "rows": 2, "columns": 3}
    ]
    at = go_to(at, "Chat")
    at.chat_input[0].set_value("Total amount?").run()
    assert asked == [up.db_path]


def test_reports_page_on_uploaded_data(tmp_path, monkeypatch):
    """Upload -> Reports: the mapping is pre-filled from the column names and a report is
    built from the uploaded table; removing the upload deletes its reports."""
    from reports import summary
    from shared.llm import FakeProvider, LLMError

    monkeypatch.setattr(summary, "get_llm", lambda: FakeProvider([LLMError("no key")]))
    lines = ["Order Date,Invoice,Customer,Category,Total"]
    for day in range(1, 29):
        for k in range(1 + day % 3):
            lines.append(f"2024-02-{day:02d},I{day}-{k},C{(day + k) % 5},Cat{k},{10 + day + k}")
    csv = write_csv(tmp_path, "shop.csv", "\n".join(lines) + "\n")
    state = new_state(tmp_path)
    load_upload(get_context(state), [csv])

    at = go_to(make_app(tmp_path, state).run(), "Reports")
    assert not at.exception
    chosen = {s.label: s.value for s in at.selectbox}
    assert chosen["Date"] == "order_date" and chosen["Amount (revenue per row)"] == "total"
    assert chosen["Order id"] == "invoice" and chosen["Category"] == "category"
    assert any("2024-02-01 to 2024-02-28" in c.value for c in at.caption)
    at = next(b for b in at.button if b.label == "Generate report").click().run()
    assert not at.exception
    assert "2024-02-28" in at.success[0].value
    folder = session_folder(get_context(state)) / "reports"
    assert (folder / "weekly_report_2024-02-28.html").exists()

    discard_upload(get_context(state))
    assert not folder.exists()


def test_reports_page_without_a_date_column(tmp_path):
    csv = write_csv(tmp_path, "people.csv", "name,age\nAna,31\n")
    state = new_state(tmp_path)
    load_upload(get_context(state), [csv])
    at = go_to(make_app(tmp_path, state).run(), "Reports")
    assert not at.exception
    assert "needs a table with a date column" in at.warning[0].value


def test_remove_button_returns_to_the_demo(tmp_path, sales_csv):
    state = new_state(tmp_path)
    up = load_upload(get_context(state), [sales_csv])
    at = go_to(make_app(tmp_path, state).run(), "Upload data")
    remove = next(b for b in at.button if b.label == "Remove uploaded data")
    at = remove.click().run()
    assert not at.exception
    assert at.sidebar.selectbox[0].value == "olist"
    assert not up.db_path.exists()
