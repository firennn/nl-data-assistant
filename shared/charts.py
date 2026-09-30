"""Render a ChartSpec with Plotly so the agent, reports and dashboard draw charts the same way."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from shared.models import ChartSpec


def render_chart(df: pd.DataFrame, spec: ChartSpec) -> go.Figure:
    """Return a Plotly figure for `df` as described by `spec`.

    Works in Streamlit (st.plotly_chart) and in HTML exports (fig.to_html()).
    """
    if spec.kind == "metric":
        column = spec.y if isinstance(spec.y, str) else df.columns[0]
        value = df[column].iloc[0] if not df.empty else None
        fig = go.Figure(go.Indicator(mode="number", value=value, title={"text": spec.title}))
    elif spec.kind == "table" or df.empty:
        fig = go.Figure(
            go.Table(
                header={"values": list(df.columns)},
                cells={"values": [df[c].tolist() for c in df.columns]},
            )
        )
        fig.update_layout(title=spec.title)
    elif spec.kind == "bar":
        fig = px.bar(df, x=spec.x, y=spec.y, color=spec.color, title=spec.title)
    elif spec.kind == "line":
        fig = px.line(df, x=spec.x, y=spec.y, color=spec.color, title=spec.title, markers=True)
    elif spec.kind == "scatter":
        fig = px.scatter(df, x=spec.x, y=spec.y, color=spec.color, title=spec.title)
    elif spec.kind == "pie":
        fig = px.pie(df, names=spec.x, values=spec.y, title=spec.title)
    else:
        raise ValueError(f"Unsupported chart kind: {spec.kind}")
    return fig
