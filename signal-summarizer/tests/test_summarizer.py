import dataclasses

import pytest
from conftest import make_message

from signal_summarizer.summarizer import (
    FALLBACK_BETA,
    SummarizationRefused,
    Summarizer,
    render_transcript,
)


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


class FakeClient:
    def __init__(self, response):
        self.messages = FakeMessages(response)
        self.beta = Beta(FakeMessages(response))


def test_summarize_sends_the_expected_request(config):
    client = FakeClient(Response([Block("Alice wants to ship on Friday.")]))
    summary = Summarizer(config, client).summarize(
        [make_message(body="ship it friday")],
        chat_label="Launch",
        window_description="the last 24 hours",
    )
    assert summary == "Alice wants to ship on Friday."

    (call,) = client.messages.calls
    assert call["model"] == "claude-opus-5"
    assert call["output_config"] == {"effort": "medium"}
    assert call["max_tokens"] == config.max_tokens
    prompt = call["messages"][0]["content"]
    assert "Conversation: Launch" in prompt
    assert "Window: the last 24 hours" in prompt
    assert "Alice: ship it friday" in prompt


def test_fallbacks_use_the_beta_endpoint(config):
    config = dataclasses.replace(config, use_fallbacks=True)
    client = FakeClient(Response([Block("done")]))
    Summarizer(config, client).summarize(
        [make_message()], chat_label="Launch", window_description="the last hour"
    )
    assert client.messages.calls == []
    (call,) = client.beta.messages.calls
    assert call["betas"] == [FALLBACK_BETA]
    assert call["fallbacks"] == "default"


def test_empty_history_short_circuits_without_an_api_call(config):
    client = FakeClient(Response([Block("unused")]))
    summary = Summarizer(config, client).summarize(
        [], chat_label="Launch", window_description="the last 24 hours"
    )
    assert "Nothing to summarize in Launch" in summary
    assert client.messages.calls == []


def test_refusal_raises(config):
    details = type("Details", (), {"category": "cyber"})()
    client = FakeClient(Response([], stop_reason="refusal", stop_details=details))
    with pytest.raises(SummarizationRefused, match="cyber"):
        Summarizer(config, client).summarize(
            [make_message()], chat_label="Launch", window_description="the last hour"
        )


def test_truncated_response_is_flagged(config):
    client = FakeClient(Response([Block("partial")], stop_reason="max_tokens"))
    summary = Summarizer(config, client).summarize(
        [make_message()], chat_label="Launch", window_description="the last hour"
    )
    assert summary.startswith("partial")
    assert "cut off at the token limit" in summary


def test_non_text_blocks_are_skipped(config):
    client = FakeClient(Response([Block("", type="thinking"), Block("real summary")]))
    summary = Summarizer(config, client).summarize(
        [make_message()], chat_label="Launch", window_description="the last hour"
    )
    assert summary == "real summary"


def test_transcript_renders_quotes_and_attachments():
    line = render_transcript([make_message(quote="Bob: thursday?", attachments=1, body="yes")])
    assert "(replying to Bob: thursday?)" in line
    assert "yes" in line
    assert "[1 attachment]" in line


def test_transcript_drops_oldest_lines_over_budget():
    messages = [make_message(timestamp=1_000 + i, body=f"message {i}") for i in range(50)]
    rendered = render_transcript(messages, max_chars=200)
    assert rendered.startswith("[... ")
    assert "earlier messages omitted" in rendered
    assert "message 49" in rendered
    assert "message 0" not in rendered
