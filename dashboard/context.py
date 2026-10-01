"""Shared state for the dashboard pages: the current data source and file locations.

This is the only place that decides which database the chat page queries. Every page gets an
AppContext; no page reads DB_PATH or picks a profile itself. The Olist demo database is always
available; uploaded databases will be added as further sources by the upload page.
"""

from __future__ import annotations

from collections.abc import Callable, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path

from evaluation.run_eval import RESULTS_DIR
from reports.export import OUTPUT_DIR as REPORTS_DIR
from shared.config import Settings, get_settings
from shared.models import DatasetProfile
from shared.profiles import OLIST_PROFILE

DASHBOARD_DIR = Path(__file__).resolve().parent
USAGE_LOG = DASHBOARD_DIR / "usage_log.jsonl"  # local only, never committed

# Keys in st.session_state. Tests can set the *_OVERRIDE keys before the app runs.
SOURCES_KEY = "data_sources"
CURRENT_KEY = "current_source"
AGENTS_KEY = "agents"
RESULTS_DIR_OVERRIDE = "results_dir"
REPORTS_DIR_OVERRIDE = "reports_dir"
USAGE_LOG_OVERRIDE = "usage_log"
AGENT_FACTORY_OVERRIDE = "agent_factory"

DEMO_KEY = "olist"


@dataclass(frozen=True)
class DataSource:
    """A database the chat page can query, with the profile the agent should use for it."""

    key: str
    name: str
    db_path: Path
    profile: DatasetProfile = field(compare=False)
    kind: str = "demo"  # demo | upload

    @property
    def is_demo(self) -> bool:
        return self.kind == "demo"


@dataclass
class AppContext:
    source: DataSource  # current database for the chat page
    demo: DataSource  # the Olist demo database, used by the Olist-only pages
    settings: Settings
    results_dir: Path
    reports_dir: Path
    usage_log: Path
    state: MutableMapping = field(repr=False)

    def agent(self, source: DataSource | None = None):
        """The agent for a source, created once per session and source."""
        source = source or self.source
        agents = self.state.setdefault(AGENTS_KEY, {})
        if source.key not in agents:
            factory: Callable = self.state.get(AGENT_FACTORY_OVERRIDE) or _make_agent
            agents[source.key] = factory(source, self.settings)
        return agents[source.key]


def _make_agent(source: DataSource, settings: Settings):
    from agent import SQLAgent

    return SQLAgent(settings=settings, db_path=source.db_path, profile=source.profile)


def demo_source(settings: Settings) -> DataSource:
    return DataSource(
        key=DEMO_KEY,
        name="Olist e-commerce (demo)",
        db_path=settings.db_path,
        profile=OLIST_PROFILE,
        kind="demo",
    )


def available_sources(state: MutableMapping, settings: Settings) -> dict[str, DataSource]:
    """All data sources in this session, the Olist demo first."""
    sources = state.setdefault(SOURCES_KEY, {})
    if DEMO_KEY not in sources:
        sources[DEMO_KEY] = demo_source(settings)
    return sources


def add_source(state: MutableMapping, source: DataSource, settings: Settings) -> None:
    """Register a data source (e.g. an uploaded database) and make it the current one."""
    available_sources(state, settings)[source.key] = source
    state[CURRENT_KEY] = source.key
    state.get(AGENTS_KEY, {}).pop(source.key, None)


def set_current_source(state: MutableMapping, key: str) -> None:
    state[CURRENT_KEY] = key


def get_context(state: MutableMapping | None = None) -> AppContext:
    """Build the context for this run of the app (defaults to st.session_state)."""
    if state is None:
        import streamlit as st

        state = st.session_state
    settings = get_settings()
    sources = available_sources(state, settings)
    current = sources.get(state.get(CURRENT_KEY, DEMO_KEY)) or sources[DEMO_KEY]
    return AppContext(
        source=current,
        demo=sources[DEMO_KEY],
        settings=settings,
        results_dir=Path(state.get(RESULTS_DIR_OVERRIDE) or RESULTS_DIR),
        reports_dir=Path(state.get(REPORTS_DIR_OVERRIDE) or REPORTS_DIR),
        usage_log=Path(state.get(USAGE_LOG_OVERRIDE) or USAGE_LOG),
        state=state,
    )
