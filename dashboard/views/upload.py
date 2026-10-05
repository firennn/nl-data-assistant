"""Upload a CSV export or a SQLite file, then ask questions about it on the Chat page.

The files become a new read-only SQLite database (data.upload.build_user_db), which is added as
the current data source. A session keeps at most one uploaded database, in its own folder
under the uploads directory. A new upload or "Remove" deletes the previous file, and folders
left behind by old sessions are deleted after UPLOAD_MAX_AGE_S.
"""

from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard.context import (
    AppContext,
    DataSource,
    add_source,
    available_sources,
    remove_source,
)
from dashboard.views import FULL_WIDTH, View
from dashboard.views.chat import CHAT_KEY
from data.upload import (
    MAX_COLUMNS,
    MAX_FILES,
    MAX_ROWS,
    MAX_UPLOAD_BYTES,
    UploadedDatabase,
    UploadError,
    build_user_db,
)

UPLOAD_KEY = "upload"  # data source key of this session's uploaded database
SESSION_KEY = "upload_session"  # random id that names this session's folder
INFO_KEY = "upload_info"  # file names, tables and notes of the current upload
UPLOAD_MAX_AGE_S = 24 * 3600
FILE_TYPES = ["csv", "sqlite", "sqlite3", "db"]
DAY_FIRST = "Day/month (03/04 = 3 April)"
MONTH_FIRST = "Month/day (03/04 = 4 March)"

PRIVACY_NOTICE = (
    "**Before you upload:** to answer a question, the table and column names, the date ranges "
    "of date columns, your questions (with the earlier ones in the conversation) and the first "
    "rows of each result are sent to the LLM provider API. Other values from your files are not "
    "sent. Do not upload personal or confidential "
    "data. The files are stored on this server only while you use them: a new upload or "
    "*Remove* deletes them, and anything left over is deleted after 24 hours."
)
LIMITS = (
    f"Up to {MAX_FILES} CSV files (one table per file) or one SQLite file, "
    f"{MAX_UPLOAD_BYTES / 1024 / 1024:g} MB in total, {MAX_ROWS:,} rows and "
    f"{MAX_COLUMNS} columns per table."
)


def session_folder(ctx: AppContext) -> Path:
    """This session's upload folder (not created here)."""
    return ctx.uploads_dir / ctx.state.setdefault(SESSION_KEY, uuid.uuid4().hex)


def remove_stale_uploads(
    root: Path,
    *,
    keep: Path | None = None,
    max_age_s: float = UPLOAD_MAX_AGE_S,
    now: float | None = None,
) -> list[Path]:
    """Delete session folders under root that were not changed for max_age_s seconds."""
    if not root.is_dir():
        return []
    now = time.time() if now is None else now
    removed = []
    for folder in root.iterdir():
        if not folder.is_dir() or folder == keep:
            continue
        try:
            age = now - folder.stat().st_mtime
        except OSError:
            continue
        if age > max_age_s:
            shutil.rmtree(folder, ignore_errors=True)
            removed.append(folder)
    return removed


def current_upload(ctx: AppContext) -> DataSource | None:
    return available_sources(ctx.state, ctx.settings).get(UPLOAD_KEY)


def discard_upload(ctx: AppContext) -> None:
    """Forget the uploaded database, its conversation and its file."""
    source = remove_source(ctx.state, UPLOAD_KEY, ctx.settings)
    ctx.state.get(CHAT_KEY, {}).pop(UPLOAD_KEY, None)
    ctx.state.pop(INFO_KEY, None)
    if source is not None:
        try:
            source.db_path.unlink(missing_ok=True)
        except OSError:
            pass  # still open on Windows; the stale-folder cleanup removes it later


def load_upload(ctx: AppContext, files, *, dayfirst: bool | None = None) -> UploadedDatabase:
    """Build a database from the files and make it the current data source, replacing an
    earlier upload. Raises UploadError (message meant for the user); on an error the earlier
    upload is kept."""
    folder = session_folder(ctx)
    remove_stale_uploads(ctx.uploads_dir, keep=folder)
    up = build_user_db(files, folder, dayfirst=dayfirst)
    discard_upload(ctx)
    source = DataSource(UPLOAD_KEY, up.profile.name, up.db_path, up.profile, "upload")
    add_source(ctx.state, source, ctx.settings)
    ctx.state[INFO_KEY] = {
        "files": [Path(str(getattr(f, "name", f))).name for f in files],
        "tables": dict(up.tables),
        "notes": list(up.notes),
    }
    return up


def tables_frame(tables: dict[str, tuple[int, int]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"table": t, "rows": rows, "columns": cols} for t, (rows, cols) in tables.items()]
    )


def show_current(ctx: AppContext) -> None:
    source = current_upload(ctx)
    if source is None:
        return
    if not source.db_path.exists():
        discard_upload(ctx)
        st.warning("Your uploaded data was deleted after 24 hours. Please upload it again.")
        return
    info = ctx.state.get(INFO_KEY, {})
    st.success(
        f"Loaded {', '.join(info.get('files', [])) or source.name}. "
        "Ask questions about it on the Chat page."
    )
    st.dataframe(tables_frame(info.get("tables", {})), hide_index=True, **FULL_WIDTH)
    notes = info.get("notes", [])
    if notes:
        with st.expander(f"What was changed or assumed ({len(notes)})", expanded=True):
            for note in notes:
                st.markdown(f"- {note}")
    if st.button("Remove uploaded data"):
        discard_upload(ctx)
        st.rerun()


def render(ctx: AppContext) -> None:
    st.header("Upload your data")
    st.caption(
        "Upload a CSV export or a SQLite database and ask questions about it in plain language. "
        "The assistant only reads the data; your files are never changed."
    )
    st.info(PRIVACY_NOTICE)
    show_current(ctx)

    files = st.file_uploader(
        "CSV or SQLite files", type=FILE_TYPES, accept_multiple_files=True, help=LIMITS
    )
    st.caption(LIMITS)
    order = st.radio(
        "Dates written like 03/04/2023 are",
        [DAY_FIRST, MONTH_FIRST],
        horizontal=True,
        help="Only used when every date in a column could be read either way.",
    )
    label = "Replace uploaded data" if current_upload(ctx) else "Load data"
    if not st.button(label, type="primary", disabled=not files):
        return
    with st.spinner("Reading the files..."):
        try:
            load_upload(ctx, files, dayfirst=None if order == DAY_FIRST else False)
        except UploadError as exc:
            st.error(str(exc))
            return
        except Exception as exc:  # the page must not crash on an unexpected file
            st.error(f"The files could not be loaded: {exc}")
            return
    st.rerun()


VIEW = View(key="upload", title="Upload data", icon="📤", order=15, render=render)
