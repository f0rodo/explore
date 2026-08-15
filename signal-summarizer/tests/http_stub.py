"""A tiny HTTP server standing in for Ollama / an OpenAI-compatible server."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class ModelServer:
    """Records requests and replies with canned JSON.

    `routes` maps a path to either a dict (sent as JSON) or a callable taking
    the decoded request body and returning (status, dict).
    """

    def __init__(self, routes: dict):
        self.routes = routes
        self.requests: list[tuple[str, dict]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep pytest output clean
                pass

            def _respond(self, status, payload):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                route = stub.routes.get(self.path)
                stub.requests.append((self.path, {}))
                if route is None:
                    self._respond(404, {"error": "not found"})
                    return
                self._respond(200, route)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length) or b"{}")
                stub.requests.append((self.path, payload))
                route = stub.routes.get(self.path)
                if route is None:
                    self._respond(404, {"error": "not found"})
                elif callable(route):
                    status, body = route(payload)
                    self._respond(status, body)
                else:
                    self._respond(200, route)

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def posts(self, path: str) -> list[dict]:
        return [body for route, body in self.requests if route == path]

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> "ModelServer":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def ollama_reply(text: str) -> dict:
    return {"model": "stub", "message": {"role": "assistant", "content": text}, "done": True}


def openai_reply(text: str) -> dict:
    return {"choices": [{"index": 0, "message": {"role": "assistant", "content": text}}]}
