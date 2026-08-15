from conftest import ACCOUNT, make_message

from signal_summarizer import cli
from signal_summarizer.store import MessageStore

NOW_MS = 1_700_000_000_000


class StubSummarizer:
    last_prompt = None

    def __init__(self, config):
        self.config = config

    def summarize(self, messages, *, chat_label, window_description):
        StubSummarizer.last_prompt = (len(messages), chat_label, window_description)
        return "everything is fine"


class StubSignalClient:
    instances = []

    def __init__(self, address, account=None, request_timeout=60.0):
        self.sent = []
        StubSignalClient.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return None

    def send_message(self, text, *, recipient=None, group_id=None):
        self.sent.append((text, recipient, group_id))


def seed(db_path, **overrides):
    with MessageStore(db_path) as store:
        store.add(make_message(timestamp=NOW_MS, **overrides))


def env(monkeypatch, db_path, **extra):
    monkeypatch.setenv("SIGNAL_ACCOUNT", ACCOUNT)
    monkeypatch.setenv("SUMMARIZER_DB", str(db_path))
    for key, value in extra.items():
        monkeypatch.setenv(key, value)


def test_missing_account_is_a_config_error(monkeypatch, capsys):
    monkeypatch.delenv("SIGNAL_ACCOUNT", raising=False)
    assert cli.main(["chats"]) == 2
    assert "configuration error" in capsys.readouterr().err


def test_chats_lists_known_conversations(monkeypatch, tmp_path, capsys):
    db = tmp_path / "s.db"
    seed(db)
    env(monkeypatch, db)
    assert cli.main(["chats"]) == 0
    out = capsys.readouterr().out
    assert "group:abc=" in out
    assert "1 msgs" in out
    assert "Launch" in out


def test_chats_with_an_empty_database(monkeypatch, tmp_path, capsys):
    env(monkeypatch, tmp_path / "empty.db")
    assert cli.main(["chats"]) == 0
    assert "No messages recorded yet." in capsys.readouterr().out


def test_digest_prints_a_summary(monkeypatch, tmp_path, capsys):
    db = tmp_path / "s.db"
    seed(db)
    env(monkeypatch, db)
    monkeypatch.setattr(cli, "Summarizer", StubSummarizer)
    monkeypatch.setattr(cli, "_now_ms", lambda: NOW_MS)

    assert cli.main(["digest", "--chat", "group:abc=", "--hours", "12"]) == 0
    out = capsys.readouterr().out
    assert "the last 12 hours (1 messages)" in out
    assert "everything is fine" in out
    assert StubSummarizer.last_prompt == (1, "Launch", "the last 12 hours")


def test_digest_by_message_count(monkeypatch, tmp_path, capsys):
    db = tmp_path / "s.db"
    seed(db)
    env(monkeypatch, db)
    monkeypatch.setattr(cli, "Summarizer", StubSummarizer)

    assert cli.main(["digest", "--chat", "group:abc=", "--messages", "1"]) == 0
    assert "the last 1 message (1 messages)" in capsys.readouterr().out
    assert StubSummarizer.last_prompt[2] == "the last 1 message"


def test_digest_without_history_exits_nonzero(monkeypatch, tmp_path, capsys):
    env(monkeypatch, tmp_path / "empty.db")
    monkeypatch.setattr(cli, "Summarizer", StubSummarizer)
    assert cli.main(["digest", "--chat", "group:abc="]) == 1
    assert "No messages recorded" in capsys.readouterr().err


def test_digest_send_posts_to_the_chat(monkeypatch, tmp_path):
    db = tmp_path / "s.db"
    seed(db)
    env(monkeypatch, db)
    monkeypatch.setattr(cli, "Summarizer", StubSummarizer)
    monkeypatch.setattr(cli, "_now_ms", lambda: NOW_MS)
    StubSignalClient.instances.clear()
    monkeypatch.setattr(cli, "SignalClient", StubSignalClient)

    assert cli.main(["digest", "--chat", "group:abc=", "--send"]) == 0
    (client,) = StubSignalClient.instances
    (text, recipient, group_id) = client.sent[0]
    assert group_id == "abc="
    assert recipient is None
    assert "everything is fine" in text


def test_digest_rejects_a_malformed_chat_id(monkeypatch, tmp_path, capsys):
    env(monkeypatch, tmp_path / "s.db")
    assert cli.main(["digest", "--chat", "not-a-chat-id"]) == 2
    assert "unrecognized chat id" in capsys.readouterr().err
