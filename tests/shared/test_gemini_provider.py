"""GeminiProvider retry and error handling, with a stand-in client (no network)."""

from types import SimpleNamespace

import pytest
from google.genai import errors

from shared.llm import (
    GeminiProvider,
    LLMError,
    LLMQuotaExceededError,
    LLMRateLimitError,
    LLMTransientError,
)


def api_error(code: int, message: str, status: str = ""):
    cls = errors.ServerError if code >= 500 else errors.ClientError
    return cls(code, {"error": {"code": code, "message": message, "status": status}})


class ScriptedModels:
    """Replaces client.models: returns or raises the scripted items in order."""

    def __init__(self, items):
        self.items = list(items)
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append((model, contents, config))
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def make_provider(items, max_retries=2):
    provider = GeminiProvider.__new__(GeminiProvider)
    provider.model = "test-model"
    provider._max_retries = max_retries
    provider.sleeps = []
    provider._sleep = provider.sleeps.append
    provider._client = SimpleNamespace(models=ScriptedModels(items))
    return provider


def ok(text="OK"):
    return SimpleNamespace(text=text, candidates=[])


def test_success_and_thinking_headroom():
    provider = make_provider([ok("hello")])
    response = provider.complete("hi", max_tokens=100, json_mode=True)
    assert response.text == "hello" and response.model == "test-model"
    config = provider._client.models.calls[0][2]
    assert config.max_output_tokens == 100 + GeminiProvider.thinking_headroom
    assert config.response_mime_type == "application/json"


def test_server_busy_is_retried_with_backoff():
    provider = make_provider([api_error(503, "busy"), api_error(503, "busy"), ok()])
    assert provider.complete("hi").text == "OK"
    assert provider.sleeps == list(GeminiProvider.server_error_backoff)


def test_server_busy_gives_up_after_retries():
    provider = make_provider([api_error(503, "busy")] * 3)
    with pytest.raises(LLMTransientError, match="server error"):
        provider.complete("hi")
    assert len(provider._client.models.calls) == 3


def test_per_minute_limit_waits_suggested_delay():
    error = api_error(429, "Quota exceeded per minute. 'retryDelay': '7s'", "RESOURCE_EXHAUSTED")
    provider = make_provider([error, ok()])
    assert provider.complete("hi").text == "OK"
    assert provider.sleeps == [8.0]


def test_daily_quota_is_not_retried():
    error = api_error(
        429, "quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier", "RESOURCE_EXHAUSTED"
    )
    provider = make_provider([error, ok()])
    with pytest.raises(LLMQuotaExceededError, match="Daily free quota for test-model"):
        provider.complete("hi")
    assert provider.sleeps == []
    assert issubclass(LLMQuotaExceededError, LLMRateLimitError)  # fallback still applies


def test_client_errors_are_not_retried():
    provider = make_provider([api_error(404, "model not found"), ok()])
    with pytest.raises(LLMError, match="request failed") as info:
        provider.complete("hi")
    assert not isinstance(info.value, LLMTransientError)
    assert provider.sleeps == []


def test_empty_response_reports_finish_reason():
    empty = SimpleNamespace(text="", candidates=[SimpleNamespace(finish_reason="MAX_TOKENS")])
    provider = make_provider([empty])
    with pytest.raises(LLMError, match="empty response.*MAX_TOKENS"):
        provider.complete("hi")
