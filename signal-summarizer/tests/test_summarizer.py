import dataclasses

import pytest
from conftest import make_message

from signal_summarizer.summarizer import Summarizer, chunk_messages, render_transcript


class FakeBackend:
    name = "fake"

    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.calls = []

    def complete(self, system, prompt):
        self.calls.append((system, prompt))
        if self.replies:
            return self.replies.pop(0)
        return f"summary {len(self.calls)}"

    def describe(self):
        return "fake backend"

    def check(self):
        return "ok"


def summarize(config, backend, messages, **kwargs):
    kwargs.setdefault("chat_label", "Launch")
    kwargs.setdefault("window_description", "the last 24 hours")
    return Summarizer(config, backend).summarize(messages, **kwargs)


def test_single_pass_prompt_contains_the_transcript(config):
    backend = FakeBackend(["Alice wants to ship on Friday."])
    result = summarize(config, backend, [make_message(body="ship it friday")])

    assert result == "Alice wants to ship on Friday."
    (system, prompt) = backend.calls[0]
    assert "You summarize Signal" in system
    assert "Conversation: Launch" in prompt
    assert "Window: the last 24 hours" in prompt
    assert "Messages: 1" in prompt
    assert "Alice: ship it friday" in prompt


def test_empty_history_makes_no_model_call(config):
    backend = FakeBackend()
    result = summarize(config, backend, [])
    assert "Nothing to summarize in Launch" in result
    assert backend.calls == []


def test_errors_from_the_backend_propagate(config):
    class Failing(FakeBackend):
        def complete(self, system, prompt):
            raise RuntimeError("model exploded")

    with pytest.raises(RuntimeError, match="model exploded"):
        summarize(config, Failing(), [make_message()])


# -- chunking (what makes small local context windows workable) --------------


def test_chunk_messages_groups_within_the_budget():
    messages = [make_message(timestamp=1000 + i, body="x" * 40) for i in range(10)]
    chunks = chunk_messages(messages, budget=200)
    assert len(chunks) > 1
    assert sum(len(chunk) for chunk in chunks) == 10
    # order is preserved across the split
    assert chunks[0][0].timestamp == 1000
    assert chunks[-1][-1].timestamp == 1009


def test_chunk_messages_keeps_one_oversized_message():
    messages = [make_message(body="x" * 5000)]
    assert chunk_messages(messages, budget=100) == [messages]


def test_long_window_is_summarized_in_chunks_then_combined(config):
    config = dataclasses.replace(config, max_transcript_chars=300)
    backend = FakeBackend()
    messages = [make_message(timestamp=1000 + i, body="y" * 60) for i in range(12)]

    result = summarize(config, backend, messages)

    prompts = [prompt for _, prompt in backend.calls]
    chunk_prompts = [p for p in prompts if "This is part" in p]
    combine_prompts = [p for p in prompts if "consecutive parts" in p]
    assert len(chunk_prompts) >= 2
    assert len(combine_prompts) == 1
    assert "part 1 of" in chunk_prompts[0]
    # the combine step is fed the partial summaries, not the raw transcript
    assert "--- part 1 ---" in combine_prompts[0]
    assert "yyyy" not in combine_prompts[0]
    # the returned text is the final combine call's answer
    assert result == f"summary {len(backend.calls)}"


def test_many_chunks_reduce_in_rounds(config):
    config = dataclasses.replace(config, max_transcript_chars=200)
    # Each partial summary is long enough that they cannot all be combined at once.
    backend = FakeBackend(["z" * 150 for _ in range(40)])
    messages = [make_message(timestamp=1000 + i, body="y" * 150) for i in range(20)]

    summarize(config, backend, messages)

    combine_calls = [p for _, p in backend.calls if "consecutive parts" in p]
    intermediate = [p for p in combine_calls if "one shorter summary" in p]
    final = [p for p in combine_calls if "summary of the whole conversation" in p]
    assert intermediate, "expected an intermediate reduce round"
    assert len(final) == 1


def test_transcript_budget_follows_the_edge_context_window(config):
    small = dataclasses.replace(config, edge_context_tokens=4096, edge_output_tokens=512)
    large = dataclasses.replace(config, edge_context_tokens=32768, edge_output_tokens=512)
    assert small.transcript_budget < large.transcript_budget
    assert small.transcript_budget < 4096 * 4  # never claims more than the window


# -- rendering ---------------------------------------------------------------


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
