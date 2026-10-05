from pathlib import Path

from streamlit.testing.v1 import AppTest

from dashboard import app, demo_db

APP = str(Path(app.__file__).resolve())


def test_existing_database_is_not_rebuilt(tmp_path):
    db = tmp_path / "olist.db"
    db.write_bytes(b"")
    assert demo_db.ensure_demo_db(db, build=lambda p: 1 / 0)


def test_missing_database_is_built_once(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_DB_AUTO_BUILD", "1")
    calls = []

    def build(path):
        calls.append(path)
        path.write_bytes(b"")

    db = tmp_path / "olist.db"
    assert demo_db.ensure_demo_db(db, build=build)
    assert demo_db.ensure_demo_db(db, build=build)
    assert calls == [db]


def test_failed_build_is_remembered_and_not_retried(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_DB_AUTO_BUILD", "1")
    calls = []

    def build(path):
        calls.append(path)
        raise RuntimeError("no internet")

    db = tmp_path / "olist.db"
    assert not demo_db.ensure_demo_db(db, build=build)
    assert not demo_db.ensure_demo_db(db, build=build)
    assert calls == [db]
    assert demo_db.build_error(db) == "no internet"


def test_auto_build_can_be_turned_off(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_DB_AUTO_BUILD", "0")
    assert not demo_db.auto_build_enabled()
    assert not demo_db.ensure_demo_db(tmp_path / "olist.db", build=lambda p: 1 / 0)


def test_pages_without_the_demo_database(tmp_path, monkeypatch):
    """Without a demo database the chat page explains it and the upload page still works."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "missing.db"))
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["uploads_dir"] = str(tmp_path / "uploads")
    at.run()
    assert not at.exception
    assert "demo database is not available" in at.warning[0].value
    assert not at.chat_input
    radio = at.sidebar.radio[0]
    at = radio.set_value(next(o for o in radio.options if o.endswith("Upload data"))).run()
    assert not at.exception and not at.warning
    assert at.button[0].label == "Load data"
