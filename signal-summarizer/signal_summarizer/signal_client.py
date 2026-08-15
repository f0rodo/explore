"""A small JSON-RPC client for a running `signal-cli daemon`.

signal-cli speaks newline-delimited JSON-RPC 2.0 over a TCP or UNIX socket.
Incoming chat traffic arrives as `receive` notifications on the same
connection that carries our request/response traffic, so a reader thread
demultiplexes the two.
"""

from __future__ import annotations

import itertools
import json
import logging
import queue
import socket
import threading
from typing import Any, Iterator, Mapping

log = logging.getLogger(__name__)

_SHUTDOWN = object()

# How often the event loop wakes to notice a shutdown. Messages arrive on the
# reader thread and are handled immediately regardless; this only bounds how
# long Ctrl-C takes, so keep it long enough to stay out of a phone's way.
EVENT_POLL_SECONDS = 5.0


class SignalRpcError(RuntimeError):
    """The daemon answered a request with a JSON-RPC error."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"signal-cli error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


def parse_address(address: str) -> tuple[int, Any]:
    """Return (socket family, connect target) for a `tcp://` or `unix://` URL."""
    if address.startswith("unix://"):
        return socket.AF_UNIX, address[len("unix://") :]
    target = address[len("tcp://") :] if address.startswith("tcp://") else address
    host, _, port = target.rpartition(":")
    if not host or not port:
        raise ValueError(f"expected host:port in {address!r}")
    return socket.AF_INET, (host, int(port))


class SignalClient:
    def __init__(
        self,
        address: str,
        *,
        account: str | None = None,
        request_timeout: float = 60.0,
    ) -> None:
        self.address = address
        self.account = account
        self.request_timeout = request_timeout
        self._sock: socket.socket | None = None
        self._rfile: Any = None
        self._wfile: Any = None
        self._reader: threading.Thread | None = None
        self._ids = itertools.count(1)
        self._write_lock = threading.Lock()
        self._pending: dict[str, queue.Queue] = {}
        self._pending_lock = threading.Lock()
        self._events: queue.Queue = queue.Queue()
        self._closing = False

    # -- connection lifecycle -------------------------------------------------

    def connect(self) -> None:
        family, target = parse_address(self.address)
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.connect(target)
        self._closing = False
        self._sock = sock
        self._rfile = sock.makefile("r", encoding="utf-8", newline="\n")
        self._wfile = sock.makefile("w", encoding="utf-8", newline="\n")
        self._events = queue.Queue()
        self._reader = threading.Thread(
            target=self._read_loop, name="signal-rpc-reader", daemon=True
        )
        self._reader.start()
        log.info("connected to signal-cli at %s", self.address)

    def close(self) -> None:
        self._closing = True
        sock, self._sock = self._sock, None

        # Shut the socket down first: that wakes the reader thread, which owns
        # the read buffer's lock. Closing the read file here instead would
        # block forever waiting for that lock.
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        reader, self._reader = self._reader, None
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=5.0)

        wfile, self._wfile = self._wfile, None
        self._rfile = None
        if wfile is not None:
            with self._write_lock:
                try:
                    wfile.close()
                except OSError:
                    pass
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

        self._fail_pending(ConnectionError("connection closed"))
        self._events.put(_SHUTDOWN)

    def __enter__(self) -> "SignalClient":
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- reader thread --------------------------------------------------------

    def _read_loop(self) -> None:
        rfile = self._rfile
        try:
            for line in rfile:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("ignoring non-JSON line from signal-cli: %r", line[:200])
                    continue
                self._dispatch(message)
        except (OSError, ValueError) as exc:  # socket closed mid-read
            if not self._closing:
                log.warning("signal-cli connection lost: %s", exc)
        finally:
            try:
                rfile.close()
            except OSError:
                pass
            self._fail_pending(ConnectionError("signal-cli connection closed"))
            self._events.put(_SHUTDOWN)

    def _dispatch(self, message: Mapping[str, Any]) -> None:
        msg_id = message.get("id")
        if msg_id is not None and ("result" in message or "error" in message):
            with self._pending_lock:
                waiter = self._pending.pop(str(msg_id), None)
            if waiter is None:
                log.debug("response for unknown request id %s", msg_id)
            else:
                waiter.put(message)
            return
        if message.get("method") == "receive":
            self._events.put(message.get("params") or {})
            return
        log.debug("ignoring signal-cli message: %s", message.get("method"))

    def _fail_pending(self, error: Exception) -> None:
        with self._pending_lock:
            waiters = list(self._pending.values())
            self._pending.clear()
        for waiter in waiters:
            waiter.put(error)

    # -- requests -------------------------------------------------------------

    def request(self, method: str, params: Mapping[str, Any] | None = None) -> Any:
        if self._wfile is None:
            raise ConnectionError("not connected to signal-cli")
        payload = dict(params or {})
        if self.account and "account" not in payload:
            payload["account"] = self.account
        request_id = str(next(self._ids))
        waiter: queue.Queue = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[request_id] = waiter
        body = json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": payload}
        )
        try:
            with self._write_lock:
                self._wfile.write(body + "\n")
                self._wfile.flush()
        except OSError as exc:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise ConnectionError(f"failed to send {method}: {exc}") from exc

        try:
            answer = waiter.get(timeout=self.request_timeout)
        except queue.Empty as exc:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise TimeoutError(f"no response to {method} within {self.request_timeout}s") from exc
        if isinstance(answer, Exception):
            raise answer
        if "error" in answer:
            error = answer["error"] or {}
            raise SignalRpcError(
                int(error.get("code", -1)), str(error.get("message", "")), error.get("data")
            )
        return answer.get("result")

    def send_message(
        self,
        text: str,
        *,
        recipient: str | None = None,
        group_id: str | None = None,
    ) -> Any:
        if bool(recipient) == bool(group_id):
            raise ValueError("pass exactly one of recipient or group_id")
        params: dict[str, Any] = {"message": text}
        if group_id:
            params["groupId"] = group_id
        else:
            params["recipient"] = [recipient]
        return self.request("send", params)

    # -- incoming traffic -----------------------------------------------------

    def events(self) -> Iterator[dict]:
        """Yield `receive` notification params until the connection drops."""
        while True:
            try:
                # A timeout rather than a bare get() so Ctrl-C stays responsive.
                item = self._events.get(timeout=EVENT_POLL_SECONDS)
            except queue.Empty:
                continue
            if item is _SHUTDOWN:
                if self._closing:
                    return
                raise ConnectionError("signal-cli connection closed")
            yield item
