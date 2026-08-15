"""Command handling and the listen loop."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .config import Config
from .envelope import Message, parse_envelope, split_chat_id
from .errors import SummarizerError, SummarizerUnavailable
from .signal_client import SignalClient
from .store import MessageStore
from .summarizer import Summarizer

log = logging.getLogger(__name__)

SUMMARY_HEADER = "\U0001f4cb Summary"
HOUR_MS = 3_600_000
PRUNE_INTERVAL_MS = HOUR_MS

_WINDOW_RE = re.compile(r"^(\d+)\s*([mhd])?$", re.IGNORECASE)
_UNIT_MS = {"m": 60_000, "h": HOUR_MS, "d": 24 * HOUR_MS}
_UNIT_NAME = {"m": "minute", "h": "hour", "d": "day"}


@dataclass(frozen=True)
class Window:
    since: int | None
    limit: int
    description: str


def parse_window(spec: str | None, *, now: int, config: Config) -> Window:
    """Parse `24h`, `90m`, `7d`, or a bare message count like `50`."""
    if not spec:
        hours = config.default_window_hours
        plural = "" if hours == 1 else "s"
        return Window(
            since=now - hours * HOUR_MS,
            limit=config.max_messages,
            description=f"the last {hours} hour{plural}",
        )

    match = _WINDOW_RE.match(spec.strip())
    if not match:
        raise ValueError(
            f"unrecognized window {spec!r} — use 30m, 12h, 7d, or a message count like 50"
        )
    amount = int(match.group(1))
    if amount <= 0:
        raise ValueError("the window has to be greater than zero")
    unit = (match.group(2) or "").lower()

    if not unit:
        count = min(amount, config.max_messages)
        plural = "" if count == 1 else "s"
        return Window(
            since=None, limit=count, description=f"the last {count} message{plural}"
        )

    plural = "" if amount == 1 else "s"
    return Window(
        since=now - amount * _UNIT_MS[unit],
        limit=config.max_messages,
        description=f"the last {amount} {_UNIT_NAME[unit]}{plural}",
    )


class SummarizerBot:
    def __init__(
        self,
        config: Config,
        client: SignalClient,
        store: MessageStore,
        summarizer: Summarizer,
        *,
        now: Callable[[], int] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.client = client
        self.store = store
        self.summarizer = summarizer
        self._now = now or (lambda: int(time.time() * 1000))
        self._sleep = sleep
        self._last_prune = 0
        self._stopped = False

    # -- event handling -------------------------------------------------------

    def handle_event(self, params: Mapping[str, Any]) -> None:
        message = parse_envelope(params, self.config.account)
        if message is None:
            return
        if not self.config.chat_allowed(message.chat_id):
            log.debug("ignoring message from unlisted chat %s", message.chat_id)
            return

        body = message.body.strip()
        if body.startswith(self.config.command_prefix):
            self.handle_command(message, body[len(self.config.command_prefix) :])
            return
        if message.from_self and body.startswith(SUMMARY_HEADER):
            return  # don't summarize our own summaries
        self.store.add(message)
        self._maybe_prune()

    def handle_command(self, message: Message, command_text: str) -> None:
        parts = command_text.split()
        if not parts:
            return
        command, args = parts[0].lower(), parts[1:]

        if command in {"summarize", "summary", "sum", "tldr"}:
            self._do_summarize(message, args[0] if args else None)
        elif command in {"help", "commands"}:
            self._reply(message.chat_id, self._help_text())
        else:
            log.debug("ignoring unknown command %r", command)

    def _do_summarize(self, message: Message, spec: str | None) -> None:
        prefix = self.config.command_prefix
        try:
            window = parse_window(spec, now=self._now(), config=self.config)
        except ValueError as exc:
            self._reply(message.chat_id, f"{exc}. Try {prefix}help.")
            return

        history = self.store.recent(
            message.chat_id, since=window.since, limit=window.limit
        )
        label = self.store.label_for(message.chat_id) or message.chat_label
        if not history:
            self._reply(
                message.chat_id,
                f"I have no messages recorded for {window.description}. "
                "I only summarize what I have seen since I started running.",
            )
            return

        log.info(
            "summarizing %d messages from %s (%s)",
            len(history),
            message.chat_id,
            window.description,
        )
        try:
            summary = self.summarizer.summarize(
                history, chat_label=label, window_description=window.description
            )
        except SummarizerUnavailable:
            # The endpoint and error belong in the operator's logs, not in a
            # chat everyone in the group can read.
            log.exception("summarizer backend unavailable for %s", message.chat_id)
            self._reply(
                message.chat_id,
                "My summarizer model isn't responding right now. "
                "The details are in my logs.",
            )
            return
        except SummarizerError as exc:
            self._reply(message.chat_id, f"I couldn't summarize that: {exc}")
            return
        except Exception:  # noqa: BLE001 - a bad model call shouldn't kill the bot
            log.exception("summarization failed for %s", message.chat_id)
            self._reply(
                message.chat_id,
                "Something went wrong while summarizing. The error is in my logs.",
            )
            return

        header = f"{SUMMARY_HEADER} of {label} - {window.description} ({len(history)} messages)"
        self._reply(message.chat_id, f"{header}\n\n{summary}")

    def _help_text(self) -> str:
        prefix = self.config.command_prefix
        hours = self.config.default_window_hours
        return (
            "I summarize this chat on request.\n\n"
            f"{prefix}summarize - the last {hours} hours\n"
            f"{prefix}summarize 90m - the last 90 minutes\n"
            f"{prefix}summarize 7d - the last 7 days\n"
            f"{prefix}summarize 50 - the last 50 messages\n"
            f"{prefix}help - this message\n\n"
            "I can only summarize messages sent while I was running."
        )

    def _reply(self, chat_id: str, text: str) -> None:
        kind, identifier = split_chat_id(chat_id)
        try:
            if kind == "group":
                self.client.send_message(text, group_id=identifier)
            else:
                self.client.send_message(text, recipient=identifier)
        except Exception:  # noqa: BLE001 - a failed send shouldn't kill the bot
            log.exception("failed to send a reply to %s", chat_id)

    def _maybe_prune(self) -> None:
        now = self._now()
        if now - self._last_prune < PRUNE_INTERVAL_MS:
            return
        self._last_prune = now
        cutoff = now - self.config.retention_days * 24 * HOUR_MS
        removed = self.store.prune(cutoff)
        if removed:
            log.info("pruned %d messages older than %d days", removed, self.config.retention_days)

    # -- main loop ------------------------------------------------------------

    def stop(self) -> None:
        self._stopped = True

    def run(self, *, max_reconnects: int | None = None) -> None:
        """Listen for messages, reconnecting with backoff if the daemon drops us."""
        backoff = 1.0
        attempts = 0
        while not self._stopped:
            try:
                self.client.connect()
                backoff = 1.0
                for params in self.client.events():
                    self.handle_event(params)
                    if self._stopped:
                        break
            except (ConnectionError, OSError) as exc:
                log.warning("lost the signal-cli connection (%s); retrying in %.0fs", exc, backoff)
            except KeyboardInterrupt:
                self._stopped = True
            finally:
                self.client.close()

            if self._stopped:
                break
            attempts += 1
            if max_reconnects is not None and attempts >= max_reconnects:
                break
            self._sleep(backoff)
            backoff = min(backoff * 2, 60.0)
