import pytest

from tests.conftest import build_fixture_db


@pytest.fixture(autouse=True)
def demo_db(tmp_path_factory, monkeypatch):
    """The dashboard's demo database is the small fixture database, and it is never
    downloaded or built during tests."""
    path = build_fixture_db(tmp_path_factory.mktemp("demo") / "olist.db")
    monkeypatch.setenv("DB_PATH", str(path))
    monkeypatch.setenv("DEMO_DB_AUTO_BUILD", "0")
    return path
