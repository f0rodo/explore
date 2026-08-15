"""One end-to-end pass: socket in, summary out."""

import json
import threading
import time

from conftest import ACCOUNT
from test_signal_client import StubDaemon, ok

from signal_summarizer.bot import SUMMARY_HEADER, SummarizerBot
from signal_summarizer.signal_client import SignalClient
from signal_summarizer.summarizer import Summarizer

NOW = 1_700_000_000_000


class CannedClaude:
    """Stands in for anthropic.Anthropic()."""

    class _Messages:
        def create(self, **kwargs):
            self.kwargs = kwargs
            block = type("Block", (), {"type": "text", "text": "Alice moved standup to 10."})()
            return type("Response", (), {"content": [block], "stop_reason": "end_turn"})()

    def __init__(self):
        self.messages = self._Messages()


def receive(text, timestamp):
    return {
        "jsonrpc": "2.0",
        "method": "receive",
        "params": {
            "envelope": {
                "sourceNumber": "+15551111111",
                "sourceName": "Alice",
                "timestamp": timestamp,
                "dataMessage": {
                    "timestamp": timestamp,
                    "message": text,
                    "groupInfo": {
                        "groupId": "Zm9vYmFy",
                        "groupName": "Standup",
                        "type": "DELIVER",
                    },
                },
            },
            "account": ACCOUNT,
        },
    }


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_message_in_command_out(config, store):
    daemon = StubDaemon(ok)
    client = SignalClient(daemon.address, account=ACCOUNT, request_timeout=5)
    bot = SummarizerBot(
        config, client, store, Summarizer(config, CannedClaude()), now=lambda: NOW
    )
    thread = threading.Thread(target=bot.run, kwargs={"max_reconnects": 1}, daemon=True)
    thread.start()
    try:
        daemon.wait_until_connected()
        daemon.push(receive("standup is at 10 tomorrow", NOW - 60_000))
        assert wait_for(lambda: store.recent("group:Zm9vYmFy")), "message was never stored"

        daemon.push(receive("!summarize 2h", NOW))
        assert wait_for(lambda: any(r["method"] == "send" for r in daemon.requests))
    finally:
        bot.stop()
        client.close()
        thread.join(timeout=5)
        daemon.close()

    (send,) = [r for r in daemon.requests if r["method"] == "send"]
    assert send["params"]["groupId"] == "Zm9vYmFy"
    text = send["params"]["message"]
    assert text.startswith(SUMMARY_HEADER)
    assert "Standup" in text
    assert "the last 2 hours (1 messages)" in text
    assert "Alice moved standup to 10." in text

    # the command itself was not recorded as chat history
    assert [m.body for m in store.recent("group:Zm9vYmFy")] == ["standup is at 10 tomorrow"]
    assert json.dumps(send)  # the request is plain JSON-RPC
