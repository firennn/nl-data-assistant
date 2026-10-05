"""Make sure the Olist demo database exists when the dashboard starts.

A fresh deployment has no database (it is not committed). If DB_PATH does not exist, the app
downloads the dataset and builds it once per server process (about a minute), unless
DEMO_DB_AUTO_BUILD is "0". If that fails, for example without internet access, the app still
runs: pages that need the demo database say so, and uploaded data works as usual.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

AUTO_BUILD_ENV = "DEMO_DB_AUTO_BUILD"

_lock = threading.Lock()
_errors: dict[Path, str] = {}  # database path -> why building it failed (once per process)


def auto_build_enabled() -> bool:
    return os.getenv(AUTO_BUILD_ENV, "1").strip().lower() not in {"0", "false", "no", "off"}


def build_error(db_path: Path) -> str | None:
    """Why the demo database could not be built in this process, if it was tried."""
    return _errors.get(Path(db_path))


def ensure_demo_db(db_path: Path, build=None) -> bool:
    """Return True if the database exists, building it first if needed and allowed.

    Only one build runs at a time; a failed build is not retried in the same process, so a
    host without internet access does not try again on every page load. `build(db_path)`
    defaults to downloading the dataset and running data.build_db.
    """
    db_path = Path(db_path)
    if db_path.exists():
        return True
    if not auto_build_enabled() or db_path in _errors:
        return False
    with _lock:
        if db_path.exists():
            return True
        if db_path in _errors:
            return False
        try:
            (build or _build)(db_path)
        except Exception as exc:  # any failure leaves the app usable for uploads
            logger.warning("Building the demo database failed: %s", exc)
            _errors[db_path] = str(exc) or type(exc).__name__
            return False
    return db_path.exists()


def _build(db_path: Path) -> None:
    from data.build_db import build_database
    from data.download import RAW_DIR, ensure_raw_data

    ensure_raw_data(RAW_DIR)
    build_database(RAW_DIR, db_path)
