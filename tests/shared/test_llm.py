from pathlib import Path

import pytest

from shared.config import Settings
from shared.llm import (
    FakeProvider,
    FallbackLLM,
    LLMConfigError,
    LLMError,
    LLMRateLimitError,
    create_provider,
    get_llm,
    register_provider,
)


def settings(**overrides) -> Settings:
    return Settings(db_path=Path("unused.db"), **overrides)


def test_fake_provider_scripted_replies():
    llm = FakeProvider(["first", "second"])
    assert llm.complete("q1").text == "first"
    assert llm.complete("q2", system="sys").text == "second"
    assert llm.calls == [("q1", None), ("q2", "sys")]
    with pytest.raises(LLMError):
        llm.complete("q3")


def test_fake_provider_function_and_errors():
    assert FakeProvider(lambda prompt, system: prompt.upper()).complete("hi").text == "HI"
    with pytest.raises(LLMRateLimitError):
        FakeProvider([LLMRateLimitError("slow down")]).complete("q")


def test_fallback_used_on_rate_limit():
    primary = FakeProvider([LLMRateLimitError("429")])
    fallback = FakeProvider(["from fallback"])
    response = FallbackLLM(primary, fallback).complete("q")
    assert response.text == "from fallback"
    assert len(primary.calls) == 1 and len(fallback.calls) == 1


def test_fallback_not_used_for_permanent_errors():
    primary = FakeProvider([LLMError("bad request")])
    fallback = FakeProvider(["unused"])
    with pytest.raises(LLMError, match="bad request"):
        FallbackLLM(primary, fallback).complete("q")
    assert fallback.calls == []


def test_unknown_provider():
    with pytest.raises(LLMConfigError, match="Unknown LLM provider"):
        create_provider("nope", "key")


def test_missing_api_key_is_config_error():
    with pytest.raises(LLMConfigError):
        get_llm(settings(llm_provider="gemini", llm_api_key=None))


def test_registered_provider_and_fallback_wiring():
    register_provider("scripted", lambda api_key, model: FakeProvider(["ok"], model=model or "m"))
    llm = get_llm(settings(llm_provider="scripted", llm_model="custom"))
    assert isinstance(llm, FakeProvider) and llm.model == "custom"

    llm = get_llm(settings(llm_provider="scripted", llm_fallback_provider="scripted"))
    assert isinstance(llm, FallbackLLM)


def test_fallback_disabled_when_it_cannot_be_created():
    register_provider("scripted", lambda api_key, model: FakeProvider(["ok"]))
    llm = get_llm(
        settings(llm_provider="scripted", llm_fallback_provider="groq", llm_fallback_api_key=None)
    )
    assert isinstance(llm, FakeProvider)
