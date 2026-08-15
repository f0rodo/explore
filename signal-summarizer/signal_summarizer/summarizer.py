"""Turn a stretch of chat history into a summary with the Claude API."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Sequence

from .config import Config
from .envelope import Message

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = """\
You summarize Signal group and direct-message threads for someone who was away \
and wants to catch up quickly.

Write the summary as plain text — Signal has no rich formatting, so use short \
paragraphs and hyphen bullets rather than Markdown headings, bold, or tables.

Cover, in this order, skipping anything the transcript does not contain:
- What was discussed, grouped by topic rather than replayed message by message.
- Decisions that were made, and who made them.
- Action items, each with the person responsible and any deadline mentioned.
- Open questions still waiting on an answer.

Name people as the transcript names them. Attribute claims to whoever made \
them rather than stating them as fact, and say when something was left \
unresolved rather than guessing at the outcome. Keep small talk to a single \
line at most. Aim for well under 400 words; a quiet thread deserves two \
sentences, not a padded report."""


class SummarizationRefused(RuntimeError):
    """The model declined to summarize this transcript."""


def format_timestamp(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).astimezone().strftime("%Y-%m-%d %H:%M")


def render_message(message: Message) -> str:
    parts = [f"[{format_timestamp(message.timestamp)}] {message.sender}:"]
    if message.quote:
        parts.append(f"(replying to {message.quote})")
    if message.body:
        parts.append(message.body)
    if message.attachments:
        noun = "attachment" if message.attachments == 1 else "attachments"
        parts.append(f"[{message.attachments} {noun}]")
    return " ".join(parts)


def render_transcript(messages: Sequence[Message], max_chars: int | None = None) -> str:
    """Render oldest-first, dropping the oldest lines if over the char budget."""
    lines = [render_message(m) for m in messages]
    if max_chars is not None:
        total = sum(len(line) + 1 for line in lines)
        dropped = 0
        while lines and total > max_chars:
            total -= len(lines.pop(0)) + 1
            dropped += 1
        if dropped:
            lines.insert(0, f"[... {dropped} earlier messages omitted ...]")
    return "\n".join(lines)


class Summarizer:
    def __init__(self, config: Config, client: Any | None = None) -> None:
        self.config = config
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic  # imported lazily so the rest of the bot works without a key

            self._client = anthropic.Anthropic()
        return self._client

    def summarize(
        self,
        messages: Sequence[Message],
        *,
        chat_label: str,
        window_description: str,
    ) -> str:
        if not messages:
            return f"Nothing to summarize in {chat_label} for {window_description}."

        transcript = render_transcript(messages, self.config.max_transcript_chars)
        prompt = (
            f"Conversation: {chat_label}\n"
            f"Window: {window_description}\n"
            f"Messages: {len(messages)}\n\n"
            f"Transcript:\n{transcript}\n\n"
            "Summarize this conversation."
        )

        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "system": SYSTEM_PROMPT,
            "output_config": {"effort": self.config.effort},
            "messages": [{"role": "user", "content": prompt}],
        }

        if self.config.use_fallbacks:
            # Claude Opus 5's safety classifiers can decline a request; server-side
            # fallbacks re-run it on Anthropic's recommended substitute model.
            response = self.client.beta.messages.create(
                betas=[FALLBACK_BETA], fallbacks="default", **kwargs
            )
        else:
            response = self.client.messages.create(**kwargs)

        if getattr(response, "stop_reason", None) == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise SummarizationRefused(
                f"the model declined to summarize this conversation (category: {category})"
            )

        text = "\n".join(
            block.text
            for block in response.content
            if getattr(block, "type", None) == "text" and getattr(block, "text", "")
        ).strip()

        if getattr(response, "stop_reason", None) == "max_tokens":
            text += "\n\n(Summary cut off at the token limit.)"
        return text or "The model returned an empty summary."
