"""Tests for autoremediate.llm with mocked OpenAI client."""
import pytest
from autoremediate import llm


class FakeChoice:
    def __init__(self, content: str):
        class Message:
            def __init__(self, c):
                self.content = c
        self.message = Message(content)


class FakeResponse:
    def __init__(self, content: str):
        self.choices = [FakeChoice(content)]


def test_missing_config_raises_exact_error(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="LLM not configured: set LLM_BASE_URL, LLM_MODEL, LLM_API_KEY in .env"):
        llm.chat([{"role": "user", "content": "hi"}])


def test_primary_success(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("LLM_MODEL", "my-primary-model")
    monkeypatch.setenv("LLM_API_KEY", "primary-key")

    class FakeClient:
        def __init__(self, base_url=None, api_key=None, timeout=None):
            class Completions:
                @staticmethod
                def create(model, messages, temperature):
                    return FakeResponse("Hello from primary")

            class Chat:
                completions = Completions()

            self.chat = Chat()

    monkeypatch.setattr(llm, "OpenAI", FakeClient)

    text, label = llm.chat([{"role": "user", "content": "ping"}])
    assert text == "Hello from primary"
    assert label == "my-primary-model"


def test_primary_raises_fallback_used(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("LLM_MODEL", "my-primary-model")
    monkeypatch.setenv("LLM_API_KEY", "primary-key")
    monkeypatch.setenv("FALLBACK_BASE_URL", "https://fallback.example.com/v1")
    monkeypatch.setenv("FALLBACK_MODEL", "my-fallback-model")
    monkeypatch.setenv("FALLBACK_API_KEY", "fallback-key")

    def fake_init(self, base_url=None, api_key=None, timeout=None):
        class Completions:
            @staticmethod
            def create(model, messages, temperature):
                if model == "my-primary-model":
                    raise ConnectionError("Primary connection failed")
                return FakeResponse("Hello from fallback")

        class Chat:
            completions = Completions()

        self.chat = Chat()

    monkeypatch.setattr(llm.OpenAI, "__init__", fake_init)

    text, label = llm.chat([{"role": "user", "content": "ping"}])
    assert text == "Hello from fallback"
    assert label == "my-fallback-model (fallback)"


def test_primary_empty_reply_fallback_used(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("LLM_MODEL", "my-primary-model")
    monkeypatch.setenv("LLM_API_KEY", "primary-key")
    monkeypatch.setenv("FALLBACK_BASE_URL", "https://fallback.example.com/v1")
    monkeypatch.setenv("FALLBACK_MODEL", "my-fallback-model")
    monkeypatch.setenv("FALLBACK_API_KEY", "fallback-key")

    def fake_init(self, base_url=None, api_key=None, timeout=None):
        class Completions:
            @staticmethod
            def create(model, messages, temperature):
                if model == "my-primary-model":
                    return FakeResponse("")
                return FakeResponse("Fallback reply")

        class Chat:
            completions = Completions()

        self.chat = Chat()

    monkeypatch.setattr(llm.OpenAI, "__init__", fake_init)

    text, label = llm.chat([{"role": "user", "content": "ping"}])
    assert text == "Fallback reply"
    assert label == "my-fallback-model (fallback)"


def test_both_fail_raises_runtime_error(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("LLM_MODEL", "my-primary-model")
    monkeypatch.setenv("LLM_API_KEY", "primary-key")
    monkeypatch.setenv("FALLBACK_BASE_URL", "https://fallback.example.com/v1")
    monkeypatch.setenv("FALLBACK_MODEL", "my-fallback-model")
    monkeypatch.setenv("FALLBACK_API_KEY", "fallback-key")

    def fake_init(self, base_url=None, api_key=None, timeout=None):
        class Completions:
            @staticmethod
            def create(model, messages, temperature):
                raise RuntimeError("Service down")

        class Chat:
            completions = Completions()

        self.chat = Chat()

    monkeypatch.setattr(llm.OpenAI, "__init__", fake_init)

    with pytest.raises(RuntimeError):
        llm.chat([{"role": "user", "content": "ping"}])
