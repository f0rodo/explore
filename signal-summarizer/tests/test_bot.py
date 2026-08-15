import dataclasses

import pytest
from conftest import ACCOUNT, make_message

from signal_summarizer.bot import SUMMARY_HEADER, SummarizerBot, parse_window
from signal_summarizer.summarizer import SummarizationRefused

NOW = 1_700_000_000_000
HOUR = 3_600_000


class FakeSignalClient:
    def __init__(self):
        self.sent = []
        self.fail_next = False

    def send_message(self, text, *, recipient=None, group_id=None):
        if self.fail_next:
            self.fail_next = False
            raise ConnectionError("daemon went away")
        self.sent.append({"text": text, "recipient": recipient, "group_id": group_id})


class FakeSummarizer:
    def __init__(self, result="a summary", error=None, model_calls=1):
        self.result = result
        self.error = error
        self.model_calls = model_calls
        self.calls = []

    def estimate_calls(self, messages):
        return self.model_calls if messages else 0

    def summarize(self, messages, *, chat_label, window_description):
        self.calls.append((list(messages), chat_label, window_description))
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def bot(config, store):
    client = FakeSignalClient()
    summarizer = FakeSummarizer()
    bot = SummarizerBot(config, client, store, summarizer, now=lambda: NOW)
    return bot


def group_event(text, *, timestamp=NOW, sender="Alice", group="Zm9v", from_self=False):
    data = {
        "timestamp": timestamp,
        "message": text,
        "groupInfo": {"groupId": group, "groupName": "Launch", "type": "DELIVER"},
    }
    envelope = {"timestamp": timestamp, "sourceNumber": "+15551111111", "sourceName": sender}
    if from_self:
        envelope["sourceNumber"] = ACCOUNT
        envelope["syncMessage"] = {"sentMessage": data}
    else:
        envelope["dataMessage"] = data
    return {"envelope": envelope, "account": ACCOUNT}


# -- window parsing ----------------------------------------------------------


@pytest.mark.parametrize(
    "spec,expected_since,expected_description",
    [
        (None, NOW - 24 * HOUR, "the last 24 hours"),
        ("6h", NOW - 6 * HOUR, "the last 6 hours"),
        ("1h", NOW - HOUR, "the last 1 hour"),
        ("90m", NOW - 90 * 60_000, "the last 90 minutes"),
        ("7d", NOW - 7 * 24 * HOUR, "the last 7 days"),
    ],
)
def test_parse_window_time_specs(config, spec, expected_since, expected_description):
    window = parse_window(spec, now=NOW, config=config)
    assert window.since == expected_since
    assert window.description == expected_description


def test_parse_window_message_count_is_capped(config):
    config = dataclasses.replace(config, max_messages=100)
    window = parse_window("500", now=NOW, config=config)
    assert window.since is None
    assert window.limit == 100
    assert window.description == "the last 100 messages"


@pytest.mark.parametrize("spec", ["yesterday", "0", "-5", "5w"])
def test_parse_window_rejects_bad_specs(config, spec):
    with pytest.raises(ValueError):
        parse_window(spec, now=NOW, config=config)


# -- storing -----------------------------------------------------------------


def test_ordinary_messages_are_stored(bot, store):
    bot.handle_event(group_event("standup moved to 10"))
    assert [m.body for m in store.recent("group:Zm9v")] == ["standup moved to 10"]


def test_commands_are_not_stored(bot, store):
    bot.handle_event(group_event("!summarize"))
    assert store.recent("group:Zm9v") == []


def test_own_summaries_are_not_stored(bot, store):
    bot.handle_event(group_event(f"{SUMMARY_HEADER} of Launch\n\nstuff happened", from_self=True))
    assert store.recent("group:Zm9v") == []


def test_unlisted_chats_are_ignored(config, store):
    config = dataclasses.replace(config, allowed_chats=frozenset({"group:other"}))
    bot = SummarizerBot(config, FakeSignalClient(), store, FakeSummarizer(), now=lambda: NOW)
    bot.handle_event(group_event("hello"))
    bot.handle_event(group_event("!summarize"))
    assert store.recent("group:Zm9v") == []
    assert bot.client.sent == []


# -- commands ----------------------------------------------------------------


def test_summarize_replies_to_the_group(bot, store):
    store.add(make_message(chat_id="group:Zm9v", timestamp=NOW - HOUR, body="first"))
    store.add(make_message(chat_id="group:Zm9v", timestamp=NOW - 60_000, body="second"))

    bot.handle_event(group_event("!summarize"))

    (sent,) = bot.client.sent
    assert sent["group_id"] == "Zm9v"
    assert sent["recipient"] is None
    assert sent["text"].startswith(SUMMARY_HEADER)
    assert "the last 24 hours (2 messages)" in sent["text"]
    assert sent["text"].endswith("a summary")

    (messages, label, description) = bot.summarizer.calls[0]
    assert [m.body for m in messages] == ["first", "second"]
    assert label == "Launch"
    assert description == "the last 24 hours"


def test_summarize_honours_the_window(bot, store):
    store.add(make_message(chat_id="group:Zm9v", timestamp=NOW - 5 * HOUR, body="old"))
    store.add(make_message(chat_id="group:Zm9v", timestamp=NOW - 60_000, body="recent"))

    bot.handle_event(group_event("!summarize 2h"))

    (messages, _, description) = bot.summarizer.calls[0]
    assert [m.body for m in messages] == ["recent"]
    assert description == "the last 2 hours"


def test_summarize_with_no_history_explains_itself(bot):
    bot.handle_event(group_event("!summarize"))
    assert bot.summarizer.calls == []
    assert "no messages recorded" in bot.client.sent[0]["text"]


def test_summarize_with_a_bad_window_explains_itself(bot, store):
    store.add(make_message(chat_id="group:Zm9v", timestamp=NOW))
    bot.handle_event(group_event("!summarize next-week"))
    assert bot.summarizer.calls == []
    assert "unrecognized window" in bot.client.sent[0]["text"]


def test_direct_message_command_replies_to_the_sender(config, store):
    bot = SummarizerBot(config, FakeSignalClient(), store, FakeSummarizer(), now=lambda: NOW)
    store.add(
        make_message(
            chat_id="dm:+15551111111", chat_kind="dm", chat_label="Alice", timestamp=NOW
        )
    )
    bot.handle_event(
        {
            "envelope": {
                "sourceNumber": "+15551111111",
                "sourceName": "Alice",
                "timestamp": NOW,
                "dataMessage": {"message": "!summarize", "timestamp": NOW},
            },
            "account": ACCOUNT,
        }
    )
    (sent,) = bot.client.sent
    assert sent["recipient"] == "+15551111111"
    assert sent["group_id"] is None


def test_help_lists_the_commands(bot):
    bot.handle_event(group_event("!help"))
    text = bot.client.sent[0]["text"]
    assert "!summarize 90m" in text
    assert "!help" in text


def test_unknown_commands_are_ignored(bot):
    bot.handle_event(group_event("!ping"))
    assert bot.client.sent == []


def test_custom_prefix(config, store):
    config = dataclasses.replace(config, command_prefix="/")
    bot = SummarizerBot(config, FakeSignalClient(), store, FakeSummarizer(), now=lambda: NOW)
    store.add(make_message(chat_id="group:Zm9v", timestamp=NOW))
    bot.handle_event(group_event("/summarize"))
    assert bot.client.sent[0]["text"].startswith(SUMMARY_HEADER)
    bot.handle_event(group_event("!summarize"))
    assert len(bot.client.sent) == 1  # the old prefix is now just chat


# -- slow on-device summaries ------------------------------------------------


def seed_history(store, count, chat_id="group:Zm9v"):
    for i in range(count):
        store.add(make_message(chat_id=chat_id, timestamp=NOW - HOUR + i, body=f"m{i}"))


def make_bot(config, store, summarizer=None, **overrides):
    config = dataclasses.replace(config, **overrides)
    return SummarizerBot(
        config, FakeSignalClient(), store, summarizer or FakeSummarizer(), now=lambda: NOW
    )


def test_no_ack_for_a_small_fast_summary(config, store):
    bot = make_bot(config, store, ack_threshold=60)
    seed_history(store, 5)
    bot.handle_event(group_event("!summarize"))
    assert len(bot.client.sent) == 1
    assert bot.client.sent[0]["text"].startswith(SUMMARY_HEADER)


def test_ack_when_the_window_is_large(config, store):
    bot = make_bot(config, store, ack_threshold=20)
    seed_history(store, 25)
    bot.handle_event(group_event("!summarize"))
    ack, summary = bot.client.sent
    assert "Summarizing 25 messages" in ack["text"]
    assert summary["text"].startswith(SUMMARY_HEADER)


def test_ack_when_the_model_needs_several_passes(config, store):
    """Multiple passes always means slow, whatever the message count."""
    bot = make_bot(config, store, summarizer=FakeSummarizer(model_calls=4), ack_threshold=60)
    seed_history(store, 5)
    bot.handle_event(group_event("!summarize"))
    assert "in 4 passes" in bot.client.sent[0]["text"]


def test_ack_can_be_disabled(config, store):
    bot = make_bot(config, store, summarizer=FakeSummarizer(model_calls=9), ack_threshold=0)
    seed_history(store, 200)
    bot.handle_event(group_event("!summarize"))
    assert len(bot.client.sent) == 1


def test_the_bots_own_replies_are_not_recorded_as_chat(config, store):
    """A linked device echoes our sends back as sync messages."""
    bot = make_bot(config, store, ack_threshold=1)
    seed_history(store, 3)
    bot.handle_event(group_event("!summarize"))
    before = len(store.recent("group:Zm9v"))

    for offset, sent in enumerate(bot.client.sent, start=1):
        bot.handle_event(group_event(sent["text"], timestamp=NOW + offset, from_self=True))

    assert len(store.recent("group:Zm9v")) == before


# -- failure handling --------------------------------------------------------


def test_refusal_is_reported_in_chat(config, store):
    summarizer = FakeSummarizer(error=SummarizationRefused("policy says no"))
    bot = SummarizerBot(config, FakeSignalClient(), store, summarizer, now=lambda: NOW)
    store.add(make_message(chat_id="group:Zm9v", timestamp=NOW))
    bot.handle_event(group_event("!summarize"))
    assert "policy says no" in bot.client.sent[0]["text"]


def test_api_errors_do_not_crash_the_bot(config, store):
    summarizer = FakeSummarizer(error=RuntimeError("500 from the API"))
    bot = SummarizerBot(config, FakeSignalClient(), store, summarizer, now=lambda: NOW)
    store.add(make_message(chat_id="group:Zm9v", timestamp=NOW - HOUR))
    bot.handle_event(group_event("!summarize"))
    assert "Something went wrong" in bot.client.sent[0]["text"]
    # and the bot keeps working afterwards
    bot.handle_event(group_event("still here"))
    assert [m.body for m in store.recent("group:Zm9v")][-1] == "still here"


def test_send_failures_do_not_crash_the_bot(bot, store):
    store.add(make_message(chat_id="group:Zm9v", timestamp=NOW))
    bot.client.fail_next = True
    bot.handle_event(group_event("!summarize"))  # must not raise
    assert bot.client.sent == []


def test_old_messages_are_pruned(config, store):
    config = dataclasses.replace(config, retention_days=1)
    bot = SummarizerBot(config, FakeSignalClient(), store, FakeSummarizer(), now=lambda: NOW)
    store.add(make_message(chat_id="group:Zm9v", timestamp=NOW - 3 * 24 * HOUR, body="ancient"))
    bot.handle_event(group_event("fresh"))
    assert [m.body for m in store.recent("group:Zm9v")] == ["fresh"]


# -- run loop ----------------------------------------------------------------


class FlakySignalClient(FakeSignalClient):
    """Drops the connection once, then delivers a message and stops."""

    def __init__(self, batches):
        super().__init__()
        self.batches = list(batches)
        self.connects = 0
        self.closes = 0

    def connect(self):
        self.connects += 1

    def close(self):
        self.closes += 1

    def events(self):
        batch = self.batches.pop(0)
        if isinstance(batch, Exception):
            raise batch
        yield from batch


def test_run_reconnects_after_a_dropped_connection(config, store):
    client = FlakySignalClient([ConnectionError("boom"), [group_event("hello")]])
    slept = []
    bot = SummarizerBot(
        config, client, store, FakeSummarizer(), now=lambda: NOW, sleep=slept.append
    )
    bot.run(max_reconnects=2)
    assert client.connects == 2
    assert client.closes == 2
    assert slept == [1.0]
    assert [m.body for m in store.recent("group:Zm9v")] == ["hello"]
