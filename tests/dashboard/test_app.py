import pytest

from dashboard import app


def test_public_interface_exists():
    for fn in ("main", "chat_tab", "reports_tab", "evaluation_tab"):
        assert callable(getattr(app, fn))


@pytest.mark.skip(reason="TODO(owner3): chat tab renders a QueryResult")
def test_chat_tab():
    """Use streamlit.testing.v1.AppTest to check the chat tab shows the SQL, table and chart."""
