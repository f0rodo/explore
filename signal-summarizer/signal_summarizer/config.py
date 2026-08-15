"""Configuration, read from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_RPC_ADDRESS = "tcp://127.0.0.1:7583"


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer, got {raw!r}") from exc


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    """Everything the bot needs to run.

    `account` is the Signal number the signal-cli daemon is registered or linked
    to. `rpc_address` points at that daemon's JSON-RPC socket.
    """

    account: str
    rpc_address: str = DEFAULT_RPC_ADDRESS
    db_path: str = "signal-summarizer.db"
    model: str = DEFAULT_MODEL
    effort: str = "medium"
    max_tokens: int = 4000
    use_fallbacks: bool = True
    command_prefix: str = "!"
    default_window_hours: int = 24
    max_messages: int = 400
    max_transcript_chars: int = 60_000
    retention_days: int = 30
    request_timeout: float = 60.0
    # Empty means "every chat the account can see".
    allowed_chats: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        env = os.environ if env is None else env
        account = env.get("SIGNAL_ACCOUNT", "").strip()
        if not account:
            raise ValueError(
                "SIGNAL_ACCOUNT is required (the phone number signal-cli is "
                "registered or linked to, e.g. +15551234567)"
            )
        allowed = {c.strip() for c in env.get("SUMMARIZER_ALLOWED_CHATS", "").split(",")}
        return cls(
            account=account,
            rpc_address=env.get("SIGNAL_RPC_ADDRESS", DEFAULT_RPC_ADDRESS).strip(),
            db_path=env.get("SUMMARIZER_DB", "signal-summarizer.db"),
            model=env.get("ANTHROPIC_MODEL", DEFAULT_MODEL).strip(),
            effort=env.get("ANTHROPIC_EFFORT", "medium").strip(),
            max_tokens=_int(env, "ANTHROPIC_MAX_TOKENS", 4000),
            use_fallbacks=_bool(env, "ANTHROPIC_FALLBACKS", True),
            command_prefix=env.get("SUMMARIZER_PREFIX", "!"),
            default_window_hours=_int(env, "SUMMARIZER_WINDOW_HOURS", 24),
            max_messages=_int(env, "SUMMARIZER_MAX_MESSAGES", 400),
            max_transcript_chars=_int(env, "SUMMARIZER_MAX_CHARS", 60_000),
            retention_days=_int(env, "SUMMARIZER_RETENTION_DAYS", 30),
            allowed_chats=frozenset(c for c in allowed if c),
        )

    def chat_allowed(self, chat_id: str) -> bool:
        return not self.allowed_chats or chat_id in self.allowed_chats
