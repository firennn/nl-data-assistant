"""Project settings, loaded from environment variables and an optional .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = "data/olist.db"


@dataclass(frozen=True)
class Settings:
    db_path: Path
    llm_provider: str = "gemini"
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_fallback_provider: str | None = None
    llm_fallback_model: str | None = None
    llm_fallback_api_key: str | None = None
    agent_max_retries: int = 2


def _env(name: str) -> str | None:
    """Return the variable's value, treating empty strings as unset."""
    value = os.getenv(name, "").strip()
    return value or None


def get_settings(env_file: str | Path | None = None) -> Settings:
    """Load settings from the environment.

    Values already set in the environment take precedence over the .env file.
    Relative DB paths are resolved against the project root.
    """
    load_dotenv(env_file or PROJECT_ROOT / ".env", override=False)

    db_path = Path(_env("DB_PATH") or DEFAULT_DB_PATH)
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path

    return Settings(
        db_path=db_path,
        llm_provider=(_env("LLM_PROVIDER") or "gemini").lower(),
        llm_model=_env("LLM_MODEL"),
        llm_api_key=_env("LLM_API_KEY"),
        llm_fallback_provider=(_env("LLM_FALLBACK_PROVIDER") or "").lower() or None,
        llm_fallback_model=_env("LLM_FALLBACK_MODEL"),
        llm_fallback_api_key=_env("LLM_FALLBACK_API_KEY"),
        agent_max_retries=int(_env("AGENT_MAX_RETRIES") or 2),
    )
