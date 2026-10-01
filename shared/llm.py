"""Thin, provider-independent LLM wrapper.

Callers only use get_llm() and the LLMProvider.complete() method, so providers can be
added, swapped or removed here without changing any other module.

Built-in providers:
- "gemini": Google Gemini API (google-genai SDK)
- "groq":   Groq API (groq SDK), typically configured as the fallback
Tests use FakeProvider, which needs no network or API key.

Adding a provider (e.g. a locally hosted fine-tuned model): write a class with `name`,
`model` and `complete()`, then call register_provider("name", factory) or add it to
_PROVIDERS. Removing one: delete its class and its _PROVIDERS entry, its SDK line in
requirements.txt and its variables in .env.example.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

from shared.config import Settings, get_settings

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """Any failure while calling an LLM provider."""


class LLMConfigError(LLMError):
    """Missing API key, unknown provider or other configuration problem."""


class LLMTransientError(LLMError):
    """Temporary failure (rate limit, server error, network). Safe to retry or fall back."""


class LLMRateLimitError(LLMTransientError):
    """The provider rejected the request because of rate or quota limits."""


class LLMQuotaExceededError(LLMRateLimitError):
    """A daily quota is used up. Waiting a few seconds will not help; a fallback provider or
    another model (with its own quota) can."""


@dataclass(frozen=True)
class LLMResponse:
    text: str
    provider: str
    model: str


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Return the model's reply to `prompt`. json_mode asks for a JSON object reply."""
        ...


_RETRY_DELAY_RE = re.compile(r"retryDelay'?\"?\s*:\s*'?\"?(\d+(?:\.\d+)?)s")


def _retry_delay_seconds(error_text: str) -> float | None:
    """Wait time suggested by the provider in a rate-limit error, if any."""
    match = _RETRY_DELAY_RE.search(error_text)
    return float(match.group(1)) if match else None


class GeminiProvider:
    name = "gemini"
    default_model = "gemini-3.1-flash-lite"
    # Newer models "think" before answering and thinking counts toward max_output_tokens.
    # This headroom keeps max_tokens meaning roughly "tokens of visible answer".
    thinking_headroom = 4096
    server_error_backoff = (2.0, 6.0)  # seconds before retry 1, 2, ...
    max_rate_limit_wait = 60.0

    def __init__(
        self,
        api_key: str | None,
        model: str | None = None,
        *,
        max_retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise LLMConfigError("Gemini API key is missing (set LLM_API_KEY).")
        from google import genai

        self.model = model or self.default_model
        self._client = genai.Client(api_key=api_key)
        self._max_retries = max_retries
        self._sleep = sleep

    def _classify(self, exc: Exception) -> tuple[LLMError, float | None]:
        """Map an SDK error to our error type and the wait before a retry (None = don't retry)."""
        text = str(exc)
        code = getattr(exc, "code", None) or 0
        if code == 429:
            if "PerDay" in text:
                return (
                    LLMQuotaExceededError(
                        f"Daily free quota for {self.model} is used up. It resets daily; "
                        f"set another model in LLM_MODEL (each model has its own quota) or "
                        f"configure a fallback provider. Details: {text[:300]}"
                    ),
                    None,
                )
            delay = _retry_delay_seconds(text) or 10.0
            return LLMRateLimitError(f"Gemini rate limit: {text}"), min(
                delay + 1.0, self.max_rate_limit_wait
            )
        if code >= 500:
            return LLMTransientError(f"Gemini server error: {text}"), 0.0
        return LLMError(f"Gemini request failed: {text}"), None

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Call the model, retrying temporary failures (server busy, per-minute limits)."""
        from google.genai import errors, types

        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            max_output_tokens=max_tokens + self.thinking_headroom,
            response_mime_type="application/json" if json_mode else None,
            # No tools are passed, so function calling is not needed.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.models.generate_content(
                    model=self.model, contents=prompt, config=config
                )
                break
            except errors.APIError as exc:
                error, wait = self._classify(exc)
            except OSError as exc:
                error, wait = LLMTransientError(f"Gemini network error: {exc}"), 0.0
            if wait is None or attempt == self._max_retries:
                raise error
            if wait == 0.0:
                backoff = self.server_error_backoff
                wait = backoff[min(attempt, len(backoff) - 1)]
            logger.warning("%s; retrying in %.0fs", str(error)[:120], wait)
            self._sleep(wait)

        text = response.text or ""
        if not text.strip():
            reason = ""
            candidates = getattr(response, "candidates", None) or []
            if candidates and getattr(candidates[0], "finish_reason", None) is not None:
                reason = f" (finish reason: {candidates[0].finish_reason})"
            raise LLMError(f"Gemini returned an empty response{reason}.")
        return LLMResponse(text=text, provider=self.name, model=self.model)


class GroqProvider:
    name = "groq"
    default_model = "openai/gpt-oss-120b"

    def __init__(self, api_key: str | None, model: str | None = None) -> None:
        if not api_key:
            raise LLMConfigError("Groq API key is missing.")
        from groq import Groq

        self.model = model or self.default_model
        self._client = Groq(api_key=api_key)

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> LLMResponse:
        import groq

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        kwargs: dict[str, object] = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
        except groq.RateLimitError as exc:
            raise LLMRateLimitError(f"Groq rate limit: {exc}") from exc
        except groq.APIConnectionError as exc:
            raise LLMTransientError(f"Groq network error: {exc}") from exc
        except groq.APIStatusError as exc:
            if exc.status_code >= 500:
                raise LLMTransientError(f"Groq server error: {exc}") from exc
            raise LLMError(f"Groq request failed: {exc}") from exc

        text = response.choices[0].message.content or ""
        if not text.strip():
            raise LLMError("Groq returned an empty response.")
        return LLMResponse(text=text, provider=self.name, model=self.model)


class FakeProvider:
    """Scripted provider for tests and offline demos.

    `replies` is consumed in order. Each item is either a string (returned as the reply)
    or an exception instance (raised). Alternatively pass a function (prompt, system) -> str.
    Every call is recorded in `calls` as (prompt, system).
    """

    name = "fake"

    def __init__(
        self,
        replies: Iterable[str | BaseException] | Callable[[str, str | None], str] = (),
        model: str = "fake-model",
    ) -> None:
        self.model = model
        self._fn = replies if callable(replies) else None
        self._replies = [] if callable(replies) else list(replies)
        self.calls: list[tuple[str, str | None]] = []

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> LLMResponse:
        self.calls.append((prompt, system))
        if self._fn is not None:
            return LLMResponse(text=self._fn(prompt, system), provider=self.name, model=self.model)
        if not self._replies:
            raise LLMError("FakeProvider has no scripted replies left.")
        reply = self._replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return LLMResponse(text=reply, provider=self.name, model=self.model)


class FallbackLLM:
    """Uses `primary`, and retries once on `fallback` if the primary fails transiently."""

    def __init__(self, primary: LLMProvider, fallback: LLMProvider) -> None:
        self.primary = primary
        self.fallback = fallback
        self.name = f"{primary.name}+{fallback.name}"
        self.model = primary.model

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> LLMResponse:
        kwargs = dict(
            system=system, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode
        )
        try:
            return self.primary.complete(prompt, **kwargs)
        except LLMTransientError as exc:
            logger.warning("Primary LLM failed (%s); using fallback %s", exc, self.fallback.name)
            return self.fallback.complete(prompt, **kwargs)


ProviderFactory = Callable[[str | None, str | None], LLMProvider]  # (api_key, model)

_PROVIDERS: dict[str, ProviderFactory] = {
    "gemini": GeminiProvider,
    "groq": GroqProvider,
}


def register_provider(name: str, factory: ProviderFactory) -> None:
    """Make a provider available to get_llm() under `name` (LLM_PROVIDER=name)."""
    _PROVIDERS[name.lower()] = factory


def available_providers() -> list[str]:
    return sorted(_PROVIDERS)


def create_provider(name: str, api_key: str | None, model: str | None = None) -> LLMProvider:
    factory = _PROVIDERS.get(name.lower())
    if factory is None:
        raise LLMConfigError(
            f"Unknown LLM provider '{name}'. Available: {', '.join(available_providers())}"
        )
    return factory(api_key, model)


def get_llm(settings: Settings | None = None) -> LLMProvider:
    """Return the configured LLM, wrapped with the fallback provider if one is configured.

    If the fallback cannot be created (e.g. missing key), the primary is used alone.
    """
    settings = settings or get_settings()
    primary = create_provider(settings.llm_provider, settings.llm_api_key, settings.llm_model)
    if not settings.llm_fallback_provider:
        return primary
    try:
        fallback = create_provider(
            settings.llm_fallback_provider,
            settings.llm_fallback_api_key,
            settings.llm_fallback_model,
        )
    except LLMConfigError as exc:
        logger.warning("Fallback LLM disabled: %s", exc)
        return primary
    return FallbackLLM(primary, fallback)
