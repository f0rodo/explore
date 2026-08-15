import dataclasses

import pytest
from http_stub import ModelServer, ollama_reply, openai_reply

from signal_summarizer.backends import build_backend
from signal_summarizer.backends.claude import FALLBACK_BETA, ClaudeBackend
from signal_summarizer.backends.edge import (
    OllamaBackend,
    OpenAICompatibleBackend,
    strip_reasoning,
)
from signal_summarizer.errors import (
    SummarizationRefused,
    SummarizerError,
    SummarizerUnavailable,
)


def edge_config(config, server_url, backend="ollama"):
    return dataclasses.replace(
        config, backend=backend, edge_endpoint=server_url, edge_model="llama3.2:3b"
    )


# -- selection ---------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("ollama", OllamaBackend),
        ("openai", OpenAICompatibleBackend),
        ("claude", ClaudeBackend),
    ],
)
def test_build_backend(config, name, expected):
    backend = build_backend(dataclasses.replace(config, backend=name))
    assert isinstance(backend, expected)


def test_build_backend_rejects_unknown_names(config):
    with pytest.raises(ValueError, match="unknown SUMMARIZER_BACKEND"):
        build_backend(dataclasses.replace(config, backend="gpt4all"))


# -- ollama ------------------------------------------------------------------


def test_ollama_request_shape(config):
    with ModelServer({"/api/chat": ollama_reply("a local summary")}) as server:
        backend = OllamaBackend(edge_config(config, server.url))
        assert backend.complete("be brief", "summarize this") == "a local summary"

    (payload,) = server.posts("/api/chat")
    assert payload["model"] == "llama3.2:3b"
    assert payload["stream"] is False
    assert payload["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "summarize this"},
    ]
    # num_ctx matters: without it Ollama truncates to the model default and the
    # oldest messages silently vanish from the summary.
    assert payload["options"]["num_ctx"] == config.edge_context_tokens
    assert payload["options"]["num_predict"] == config.edge_output_tokens


def test_ollama_reports_model_errors(config):
    with ModelServer({"/api/chat": {"error": 'model "llama3.2:3b" not found'}}) as server:
        backend = OllamaBackend(edge_config(config, server.url))
        with pytest.raises(SummarizerError, match="not found"):
            backend.complete("s", "p")


def test_ollama_rejects_an_empty_answer(config):
    with ModelServer({"/api/chat": ollama_reply("   ")}) as server:
        backend = OllamaBackend(edge_config(config, server.url))
        with pytest.raises(SummarizerError, match="empty summary"):
            backend.complete("s", "p")


def test_ollama_check_requires_the_model_to_be_installed(config):
    routes = {"/api/tags": {"models": [{"name": "mistral:7b"}]}}
    with ModelServer(routes) as server:
        backend = OllamaBackend(edge_config(config, server.url))
        with pytest.raises(SummarizerUnavailable, match="ollama pull"):
            backend.check()

    routes = {"/api/tags": {"models": [{"name": "llama3.2:3b"}]}}
    with ModelServer(routes) as server:
        backend = OllamaBackend(edge_config(config, server.url))
        assert "installed" in backend.check()


def test_ollama_check_tolerates_a_missing_tag(config):
    """Users configure `llama3.2`; ollama reports `llama3.2:latest`."""
    routes = {"/api/tags": {"models": [{"name": "llama3.2:latest"}]}}
    with ModelServer(routes) as server:
        backend = OllamaBackend(
            dataclasses.replace(config, edge_endpoint=server.url, edge_model="llama3.2")
        )
        assert "installed" in backend.check()


def test_unreachable_server_is_reported_as_unavailable(config):
    backend = OllamaBackend(
        dataclasses.replace(config, edge_endpoint="http://127.0.0.1:1", edge_timeout=2)
    )
    with pytest.raises(SummarizerUnavailable, match="could not reach"):
        backend.complete("s", "p")


# -- openai-compatible -------------------------------------------------------


def test_openai_request_shape_and_auth(config):
    with ModelServer({"/chat/completions": openai_reply("a local summary")}) as server:
        backend = OpenAICompatibleBackend(
            dataclasses.replace(
                config,
                backend="openai",
                edge_endpoint=server.url,
                edge_model="qwen2.5:7b",
                edge_api_key="secret",
            )
        )
        assert backend.complete("be brief", "summarize this") == "a local summary"

    (payload,) = server.posts("/chat/completions")
    assert payload["model"] == "qwen2.5:7b"
    assert payload["max_tokens"] == config.edge_output_tokens
    assert payload["temperature"] == config.edge_temperature
    assert payload["messages"][0]["role"] == "system"


def test_openai_http_error_is_reported(config):
    routes = {"/chat/completions": lambda body: (500, {"error": "out of memory"})}
    with ModelServer(routes) as server:
        backend = OpenAICompatibleBackend(
            dataclasses.replace(config, backend="openai", edge_endpoint=server.url)
        )
        with pytest.raises(SummarizerError, match="HTTP 500"):
            backend.complete("s", "p")


def test_openai_check_reports_served_models(config):
    routes = {"/models": {"data": [{"id": "local-model"}]}}
    with ModelServer(routes) as server:
        backend = OpenAICompatibleBackend(
            dataclasses.replace(config, backend="openai", edge_endpoint=server.url)
        )
        assert "local-model" in backend.check()


# -- reasoning models --------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("<think>hmm, who said what</think>\nThe summary.", "The summary."),
        ("<THINK>x</THINK>The summary.", "The summary."),
        ("<reasoning>x</reasoning>\n\nThe summary.", "The summary."),
        ("The summary.", "The summary."),
        ("The summary.\n<think>ran out of tokens", "The summary."),
        ("<think>a</think>One.<think>b</think>Two.", "One.Two."),
    ],
)
def test_strip_reasoning(raw, expected):
    assert strip_reasoning(raw) == expected


def test_ollama_strips_reasoning_blocks(config):
    reply = ollama_reply("<think>let me see</think>\nAlice moved standup.")
    with ModelServer({"/api/chat": reply}) as server:
        backend = OllamaBackend(edge_config(config, server.url))
        assert backend.complete("s", "p") == "Alice moved standup."


# -- claude ------------------------------------------------------------------


class Block:
    def __init__(self, text, type="text"):
        self.text = text
        self.type = type


class Response:
    def __init__(self, blocks, stop_reason="end_turn", stop_details=None):
        self.content = blocks
        self.stop_reason = stop_reason
        self.stop_details = stop_details


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class Beta:
    def __init__(self, messages):
        self.messages = messages


class FakeAnthropic:
    def __init__(self, response):
        self.messages = FakeMessages(response)
        self.beta = Beta(FakeMessages(response))


def claude_config(config, **overrides):
    return dataclasses.replace(config, backend="claude", **overrides)


def test_claude_request_shape(config):
    client = FakeAnthropic(Response([Block("Alice wants to ship on Friday.")]))
    backend = ClaudeBackend(claude_config(config), client)
    assert backend.complete("be brief", "summarize") == "Alice wants to ship on Friday."

    (call,) = client.messages.calls
    assert call["model"] == "claude-opus-5"
    assert call["output_config"] == {"effort": "medium"}
    assert call["system"] == "be brief"


def test_claude_fallbacks_use_the_beta_endpoint(config):
    client = FakeAnthropic(Response([Block("done")]))
    backend = ClaudeBackend(claude_config(config, use_fallbacks=True), client)
    backend.complete("s", "p")
    assert client.messages.calls == []
    (call,) = client.beta.messages.calls
    assert call["betas"] == [FALLBACK_BETA]
    assert call["fallbacks"] == "default"


def test_claude_refusal_raises(config):
    details = type("Details", (), {"category": "cyber"})()
    client = FakeAnthropic(Response([], stop_reason="refusal", stop_details=details))
    with pytest.raises(SummarizationRefused, match="cyber"):
        ClaudeBackend(claude_config(config), client).complete("s", "p")


def test_claude_truncation_is_flagged(config):
    client = FakeAnthropic(Response([Block("partial")], stop_reason="max_tokens"))
    text = ClaudeBackend(claude_config(config), client).complete("s", "p")
    assert text.startswith("partial")
    assert "cut off at the token limit" in text


def test_claude_skips_non_text_blocks(config):
    client = FakeAnthropic(Response([Block("", type="thinking"), Block("real summary")]))
    assert ClaudeBackend(claude_config(config), client).complete("s", "p") == "real summary"
