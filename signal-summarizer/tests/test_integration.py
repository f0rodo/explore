"""End-to-end passes: socket in, summary out."""

import dataclasses
import json
import threading
import time

from conftest import ACCOUNT
from http_stub import ModelServer, ollama_reply
from test_signal_client import StubDaemon, ok

from signal_summarizer.bot import SUMMARY_HEADER, SummarizerBot
from signal_summarizer.signal_client import SignalClient
from signal_summarizer.summarizer import Summarizer

NOW = 1_700_000_000_000


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


def test_message_in_summary_out_via_a_local_model(config, store):
    """Signal socket -> store -> local model over HTTP -> reply on the socket."""
    model = ModelServer({"/api/chat": ollama_reply("Alice moved standup to 10.")})
    config = dataclasses.replace(config, backend="ollama", edge_endpoint=model.url)

    daemon = StubDaemon(ok)
    client = SignalClient(daemon.address, account=ACCOUNT, request_timeout=5)
    bot = SummarizerBot(config, client, store, Summarizer(config), now=lambda: NOW)
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
        model.close()

    (send,) = [r for r in daemon.requests if r["method"] == "send"]
    assert send["params"]["groupId"] == "Zm9vYmFy"
    text = send["params"]["message"]
    assert text.startswith(SUMMARY_HEADER)
    assert "Standup" in text
    assert "the last 2 hours (1 messages)" in text
    assert "Alice moved standup to 10." in text

    # the transcript went to the local endpoint, and only there
    (payload,) = model.posts("/api/chat")
    assert "standup is at 10 tomorrow" in payload["messages"][1]["content"]

    # the command itself was not recorded as chat history
    assert [m.body for m in store.recent("group:Zm9vYmFy")] == ["standup is at 10 tomorrow"]
    assert json.dumps(send)  # the request is plain JSON-RPC


def test_phone_profile_end_to_end(config, store, tmp_path):
    """The on-device shape: no resident server, a subprocess per model call."""
    from test_command_backend import fake_model

    llama = fake_model(
        tmp_path,
        """
import sys
prompt = sys.stdin.read()
sys.stderr.write("llama_perf_context_print: 14.2 tokens per second\\n")
print("Standup moved to 10.")
print("[end of text]")
""",
    )
    config = dataclasses.replace(
        config,
        backend="command",
        edge_command=f"{llama} -c 4096 -n 512 -f /dev/stdin",
        ack_threshold=1,  # a phone is slow enough to always say "working on it"
    )

    daemon = StubDaemon(ok)
    client = SignalClient(daemon.address, account=ACCOUNT, request_timeout=5)
    bot = SummarizerBot(config, client, store, Summarizer(config), now=lambda: NOW)
    thread = threading.Thread(target=bot.run, kwargs={"max_reconnects": 1}, daemon=True)
    thread.start()
    try:
        daemon.wait_until_connected()
        daemon.push(receive("standup is at 10 tomorrow", NOW - 60_000))
        assert wait_for(lambda: store.recent("group:Zm9vYmFy"))
        daemon.push(receive("!summarize 2h", NOW))
        assert wait_for(lambda: len([r for r in daemon.requests if r["method"] == "send"]) == 2)
    finally:
        bot.stop()
        client.close()
        thread.join(timeout=5)
        daemon.close()

    ack, summary = [r["params"]["message"] for r in daemon.requests if r["method"] == "send"]
    assert "Summarizing 1 messages" in ack
    assert summary.startswith(SUMMARY_HEADER)
    assert "Standup moved to 10." in summary


def test_local_model_down_is_reported_in_chat(config, store):
    config = dataclasses.replace(
        config, backend="ollama", edge_endpoint="http://127.0.0.1:1", edge_timeout=2
    )
    daemon = StubDaemon(ok)
    client = SignalClient(daemon.address, account=ACCOUNT, request_timeout=5)
    bot = SummarizerBot(config, client, store, Summarizer(config), now=lambda: NOW)
    thread = threading.Thread(target=bot.run, kwargs={"max_reconnects": 1}, daemon=True)
    thread.start()
    try:
        daemon.wait_until_connected()
        daemon.push(receive("standup is at 10 tomorrow", NOW - 60_000))
        assert wait_for(lambda: store.recent("group:Zm9vYmFy"))
        daemon.push(receive("!summarize 2h", NOW))
        assert wait_for(lambda: any(r["method"] == "send" for r in daemon.requests))
    finally:
        bot.stop()
        client.close()
        thread.join(timeout=5)
        daemon.close()

    (send,) = [r for r in daemon.requests if r["method"] == "send"]
    message = send["params"]["message"]
    assert "isn't responding" in message
    # the endpoint stays in the logs rather than going out to the group
    assert "127.0.0.1" not in message
