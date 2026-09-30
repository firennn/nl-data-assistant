from datetime import date

import pandas as pd
import plotly.graph_objects as go
import pytest

from shared.charts import render_chart
from shared.config import PROJECT_ROOT, get_settings
from shared.models import ChartSpec, MetricResult, QueryResult


def test_metric_change_pct():
    m = MetricResult("revenue", 110.0, date(2018, 1, 1), date(2018, 1, 7), comparison_value=100.0)
    assert m.change_pct == pytest.approx(10.0)
    assert MetricResult("x", 1.0, date(2018, 1, 1), date(2018, 1, 7)).change_pct is None
    assert MetricResult("x", 1.0, date(2018, 1, 1), date(2018, 1, 7), 0.0).change_pct is None


def test_query_result_states():
    assert QueryResult("q", sql="SELECT 1", data=pd.DataFrame({"a": [1]})).ok
    assert not QueryResult("q", error="boom").ok
    assert not QueryResult("q", needs_clarification=True, clarifying_question="Which year?").ok


def test_settings_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", "custom/path.db")
    monkeypatch.setenv("LLM_PROVIDER", "Groq")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "")
    monkeypatch.setenv("AGENT_MAX_RETRIES", "5")
    s = get_settings(env_file=tmp_path / "none.env")
    assert s.db_path == PROJECT_ROOT / "custom" / "path.db"
    assert s.llm_provider == "groq"
    assert s.llm_fallback_provider is None
    assert s.agent_max_retries == 5


@pytest.mark.parametrize(
    "spec",
    [
        ChartSpec("bar", "t", x="category", y="n"),
        ChartSpec("line", "t", x="category", y="n"),
        ChartSpec("scatter", "t", x="n", y="m"),
        ChartSpec("pie", "t", x="category", y="n"),
        ChartSpec("table", "t"),
        ChartSpec("metric", "t", y="n"),
    ],
)
def test_render_chart_kinds(spec):
    df = pd.DataFrame({"category": ["a", "b"], "n": [1, 2], "m": [3, 4]})
    assert isinstance(render_chart(df, spec), go.Figure)
