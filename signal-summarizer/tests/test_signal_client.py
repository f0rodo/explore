"""The JSON-RPC client, driven against a stub daemon on a real socket."""

import json
import socket
import threading

import pytest

from signal_summarizer.signal_client import SignalClient, SignalRpcError, parse_address


class StubDaemon:
    """Accepts one connection, answers requests, can push notifications."""

    def __init__(self, responder):
        self.responder = responder
        self.requests = []
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(1)
        self.address = "tcp://127.0.0.1:%d" % self._server.getsockname()[1]
        self._conn = None
        self._wfile = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        conn, _ = self._server.accept()
        self._conn = conn
        self._wfile = conn.makefile("w", encoding="utf-8", newline="\n")
        self._ready.set()
        with conn.makefile("r", encoding="utf-8", newline="\n") as rfile:
            for line in rfile:
                line = line.strip()
                if not line:
                    continue
                request = json.loads(line)
                self.requests.append(request)
                reply = self.responder(request)
                if reply is not None:
                    self.push(reply)

    def push(self, payload):
        self._ready.wait(5)
        self._wfile.write(json.dumps(payload) + "\n")
        self._wfile.flush()

    def wait_until_connected(self):
        assert self._ready.wait(5), "client never connected"

    def close(self):
        if self._conn is not None:
            # shutdown, not just close: the makefile handles hold their own
            # reference to the socket, so close() alone would not send a FIN.
            try:
                self._conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._conn.close()
            except OSError:
                pass
        self._server.close()


def ok(request):
    return {"jsonrpc": "2.0", "id": request["id"], "result": {"timestamp": 1234}}


def test_parse_address():
    assert parse_address("tcp://127.0.0.1:7583") == (socket.AF_INET, ("127.0.0.1", 7583))
    assert parse_address("127.0.0.1:7583") == (socket.AF_INET, ("127.0.0.1", 7583))
    assert parse_address("unix:///tmp/signal.sock") == (socket.AF_UNIX, "/tmp/signal.sock")
    with pytest.raises(ValueError):
        parse_address("nonsense")


def test_send_message_to_group_and_recipient():
    daemon = StubDaemon(ok)
    try:
        with SignalClient(daemon.address, account="+15550000000") as client:
            assert client.send_message("hi", group_id="Zm9v") == {"timestamp": 1234}
            client.send_message("hi", recipient="+15551111111")
    finally:
        daemon.close()

    group_request, dm_request = daemon.requests
    assert group_request["method"] == "send"
    assert group_request["params"] == {
        "message": "hi",
        "groupId": "Zm9v",
        "account": "+15550000000",
    }
    assert dm_request["params"]["recipient"] == ["+15551111111"]


def test_send_message_requires_exactly_one_target():
    client = SignalClient("tcp://127.0.0.1:1")
    with pytest.raises(ValueError):
        client.send_message("hi")
    with pytest.raises(ValueError):
        client.send_message("hi", recipient="+1", group_id="g")


def test_rpc_errors_are_raised():
    def responder(request):
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            "error": {"code": -32602, "message": "Unregistered user"},
        }

    daemon = StubDaemon(responder)
    try:
        with SignalClient(daemon.address) as client:
            with pytest.raises(SignalRpcError) as excinfo:
                client.send_message("hi", recipient="+15551111111")
        assert excinfo.value.code == -32602
        assert "Unregistered user" in str(excinfo.value)
    finally:
        daemon.close()


def test_notifications_are_delivered_while_requests_are_in_flight():
    daemon = StubDaemon(ok)
    received = []
    try:
        with SignalClient(daemon.address) as client:
            daemon.wait_until_connected()
            daemon.push(
                {
                    "jsonrpc": "2.0",
                    "method": "receive",
                    "params": {"envelope": {"timestamp": 1}},
                }
            )
            client.send_message("hi", recipient="+15551111111")  # response, not an event
            daemon.push(
                {
                    "jsonrpc": "2.0",
                    "method": "receive",
                    "params": {"envelope": {"timestamp": 2}},
                }
            )
            for params in client.events():
                received.append(params["envelope"]["timestamp"])
                if len(received) == 2:
                    break
    finally:
        daemon.close()
    assert received == [1, 2]


def test_events_raise_when_the_daemon_disappears():
    daemon = StubDaemon(ok)
    client = SignalClient(daemon.address)
    client.connect()
    daemon.wait_until_connected()
    daemon.close()
    with pytest.raises(ConnectionError):
        for _ in client.events():
            pass
    client.close()


def test_request_without_a_connection_raises():
    with pytest.raises(ConnectionError):
        SignalClient("tcp://127.0.0.1:1").request("send", {})


def test_request_times_out_when_the_daemon_stays_silent():
    daemon = StubDaemon(lambda request: None)  # never answers
    try:
        with SignalClient(daemon.address, request_timeout=0.2) as client:
            with pytest.raises(TimeoutError):
                client.send_message("hi", recipient="+15551111111")
    finally:
        daemon.close()


def test_malformed_lines_are_skipped():
    daemon = StubDaemon(ok)
    try:
        with SignalClient(daemon.address) as client:
            daemon.wait_until_connected()
            daemon._wfile.write("this is not json\n")
            daemon._wfile.flush()
            assert client.send_message("hi", recipient="+1") == {"timestamp": 1234}
    finally:
        daemon.close()
