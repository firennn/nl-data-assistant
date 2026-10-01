"""Shared contract used by every module: config, read-only DB access, schema, models, LLM.

Modules depend on shared/ and on the database only, never on each other's internals.
See docs/ARCHITECTURE.md.
"""

from shared.config import Settings, get_settings
from shared.db import QueryError, QueryTimeoutError, UnsafeQueryError, run_query, validate_read_only
from shared.llm import LLMError, LLMProvider, LLMResponse, get_llm
from shared.models import ChartSpec, DatasetProfile, MetricResult, QueryResult
from shared.schema import SchemaInfo, describe_schema, schema_to_prompt

__all__ = [
    "ChartSpec",
    "DatasetProfile",
    "LLMError",
    "LLMProvider",
    "LLMResponse",
    "MetricResult",
    "QueryError",
    "QueryResult",
    "QueryTimeoutError",
    "SchemaInfo",
    "Settings",
    "UnsafeQueryError",
    "describe_schema",
    "get_llm",
    "get_settings",
    "run_query",
    "schema_to_prompt",
    "validate_read_only",
]
