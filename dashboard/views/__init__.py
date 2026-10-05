"""Dashboard pages. Each module in this folder defines one page as `VIEW = View(...)`.

Adding a page means adding one file here; the app finds it automatically. (The folder is not
called `pages/`, because Streamlit would treat that name as its own multipage app.)
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass

import streamlit as st

from dashboard.context import AppContext


def _full_width() -> dict:
    """Keyword for full-width elements: `width="stretch"` replaces the deprecated
    `use_container_width=True` in newer Streamlit; older versions (requirements allow
    1.35+) only know the old keyword."""
    major, minor = (int(x) for x in st.__version__.split(".")[:2])
    return {"width": "stretch"} if (major, minor) >= (1, 50) else {"use_container_width": True}


FULL_WIDTH = _full_width()


@dataclass(frozen=True)
class View:
    key: str
    title: str
    render: Callable[[AppContext], None]
    order: int = 100  # position in the menu
    icon: str = ""
    olist_only: bool = False  # always uses the Olist demo database, whatever is selected
    needs_database: bool = True  # False for pages that work without any database file


def discover() -> list[View]:
    """All pages in this package, sorted by `order`. Fails on a missing VIEW or duplicate key."""
    views: dict[str, View] = {}
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        view = getattr(module, "VIEW", None)
        if not isinstance(view, View):
            raise TypeError(f"dashboard/views/{info.name}.py must define VIEW = View(...)")
        if view.key in views:
            raise ValueError(f"duplicate page key {view.key!r} in dashboard/views/{info.name}.py")
        views[view.key] = view
    return sorted(views.values(), key=lambda v: (v.order, v.title))


def get_view(key: str) -> View:
    for view in discover():
        if view.key == key:
            return view
    raise KeyError(f"no dashboard page with key {key!r}")
