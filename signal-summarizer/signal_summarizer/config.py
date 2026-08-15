"""Configuration, read from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_RPC_ADDRESS = "tcp://127.0.0.1:7583"
DEFAULT_BACKEND = "ollama"
DEFAULT_EDGE_MODEL = "llama3.2:3b"

# Where each edge runtime listens out of the box.
EDGE_ENDPOINTS = {
    "ollama": "http://127.0.0.1:11434",
    "openai": "http://127.0.0.1:8080/v1",
}

# Rough characters-per-token for chat text, used to turn a context window into
# a transcript budget. Deliberately conservative — overflowing the context
# silently drops the oldest messages from the summary.
CHARS_PER_TOKEN = 3.5


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer, got {raw!r}") from exc


def _float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be a number, got {raw!r}") from exc


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_backend(name: str) -> str:
    name = name.strip().lower()
    if name in {"openai-compatible", "llama.cpp", "llamacpp", "lmstudio", "vllm"}:
        return "openai"
    if name in {"anthropic", "api"}:
        return "claude"
    if name in {"subprocess", "llama-cli", "exec"}:
        return "command"
    if name in {"ollama", "openai", "command", "claude"}:
        return name
    raise ValueError(
        f"SUMMARIZER_BACKEND must be ollama, openai, command, or claude — got {name!r}"
    )


@dataclass(frozen=True)
class Config:
    """Everything the bot needs to run.

    `account` is the Signal number the signal-cli daemon is registered or linked
    to. `rpc_address` points at that daemon's JSON-RPC socket.
    """

    account: str
    rpc_address: str = DEFAULT_RPC_ADDRESS
    db_path: str = "signal-summarizer.db"

    # Which model summarizes: "ollama" or "openai" keep everything on this
    # machine; "claude" sends the transcript to the Anthropic API.
    backend: str = DEFAULT_BACKEND

    # Edge backends
    edge_endpoint: str = EDGE_ENDPOINTS[DEFAULT_BACKEND]
    edge_model: str = DEFAULT_EDGE_MODEL
    # For backend="command": the program to run, one process per model call.
    edge_command: str = ""
    edge_context_tokens: int = 8192
    edge_output_tokens: int = 1024
    edge_temperature: float = 0.2
    edge_timeout: float = 300.0
    edge_api_key: str | None = None

    # Claude backend
    model: str = DEFAULT_MODEL
    effort: str = "medium"
    max_tokens: int = 4000
    use_fallbacks: bool = True

    command_prefix: str = "!"
    default_window_hours: int = 24
    max_messages: int = 400
    # Say "working on it" before a summary this many messages or larger, so a
    # slow on-device model doesn't look like a dead bot. 0 disables it.
    ack_threshold: int = 60
    # 0 means "derive it from the backend's context window".
    max_transcript_chars: int = 0
    retention_days: int = 30
    request_timeout: float = 60.0
    # Empty means "every chat the account can see".
    allowed_chats: frozenset[str] = field(default_factory=frozenset)

    @property
    def transcript_budget(self) -> int:
        """How many characters of transcript to put in one model call."""
        if self.max_transcript_chars > 0:
            return self.max_transcript_chars
        if self.backend == "claude":
            return 60_000
        # Leave room for the system prompt, the framing, and the answer itself.
        reserve = self.edge_output_tokens + 400
        usable = max(self.edge_context_tokens - reserve, 512)
        return int(usable * CHARS_PER_TOKEN)

    @property
    def is_edge(self) -> bool:
        return self.backend != "claude"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        env = os.environ if env is None else env
        account = env.get("SIGNAL_ACCOUNT", "").strip()
        if not account:
            raise ValueError(
                "SIGNAL_ACCOUNT is required (the phone number signal-cli is "
                "registered or linked to, e.g. +15551234567)"
            )
        backend = _normalize_backend(env.get("SUMMARIZER_BACKEND", DEFAULT_BACKEND))
        endpoint = (
            env.get("EDGE_ENDPOINT", "").strip()
            or (env.get("OLLAMA_HOST", "").strip() if backend == "ollama" else "")
            or EDGE_ENDPOINTS.get(backend, EDGE_ENDPOINTS["ollama"])
        )
        if not endpoint.startswith(("http://", "https://")):
            endpoint = f"http://{endpoint}"  # OLLAMA_HOST is often a bare host:port
        allowed = {c.strip() for c in env.get("SUMMARIZER_ALLOWED_CHATS", "").split(",")}
        return cls(
            account=account,
            rpc_address=env.get("SIGNAL_RPC_ADDRESS", DEFAULT_RPC_ADDRESS).strip(),
            db_path=env.get("SUMMARIZER_DB", "signal-summarizer.db"),
            backend=backend,
            edge_endpoint=endpoint,
            edge_model=env.get("EDGE_MODEL", DEFAULT_EDGE_MODEL).strip(),
            edge_command=env.get("EDGE_COMMAND", ""),
            edge_context_tokens=_int(env, "EDGE_CONTEXT_TOKENS", 8192),
            edge_output_tokens=_int(env, "EDGE_OUTPUT_TOKENS", 1024),
            edge_temperature=_float(env, "EDGE_TEMPERATURE", 0.2),
            edge_timeout=_float(env, "EDGE_TIMEOUT", 300.0),
            edge_api_key=env.get("EDGE_API_KEY") or None,
            model=env.get("ANTHROPIC_MODEL", DEFAULT_MODEL).strip(),
            effort=env.get("ANTHROPIC_EFFORT", "medium").strip(),
            max_tokens=_int(env, "ANTHROPIC_MAX_TOKENS", 4000),
            use_fallbacks=_bool(env, "ANTHROPIC_FALLBACKS", True),
            command_prefix=env.get("SUMMARIZER_PREFIX", "!"),
            default_window_hours=_int(env, "SUMMARIZER_WINDOW_HOURS", 24),
            max_messages=_int(env, "SUMMARIZER_MAX_MESSAGES", 400),
            ack_threshold=_int(env, "SUMMARIZER_ACK_MESSAGES", 60),
            max_transcript_chars=_int(env, "SUMMARIZER_MAX_CHARS", 0),
            retention_days=_int(env, "SUMMARIZER_RETENTION_DAYS", 30),
            allowed_chats=frozenset(c for c in allowed if c),
        )

    def chat_allowed(self, chat_id: str) -> bool:
        return not self.allowed_chats or chat_id in self.allowed_chats
