"""Turn a stretch of chat history into a summary.

The prompt shapes live here; the model call itself is a backend (see
`backends/`), so a local model and the Claude API get the same instructions.
A window that does not fit the model's context is summarized in chunks and
those partial summaries are then combined, which is what makes small local
models workable on long conversations.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Sequence

from .backends import Backend, build_backend
from .config import Config
from .envelope import Message
from .errors import SummarizationRefused, SummarizerError, SummarizerUnavailable

log = logging.getLogger(__name__)

MAX_REDUCE_ROUNDS = 4

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
sentences, not a padded report.

Reply with the summary itself. No preamble, and no commentary about the task."""


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


def chunk_messages(
    messages: Sequence[Message], budget: int
) -> list[list[Message]]:
    """Split messages into consecutive groups that each fit the char budget."""
    chunks: list[list[Message]] = []
    current: list[Message] = []
    size = 0
    for message in messages:
        length = len(render_message(message)) + 1
        if current and size + length > budget:
            chunks.append(current)
            current, size = [], 0
        current.append(message)
        size += length
    if current:
        chunks.append(current)
    return chunks or [[]]


def _batch_texts(texts: Sequence[str], budget: int) -> list[list[str]]:
    """Group already-summarized parts so each batch fits the char budget."""
    batches: list[list[str]] = []
    current: list[str] = []
    size = 0
    for text in texts:
        length = len(text) + 2
        if current and size + length > budget:
            batches.append(current)
            current, size = [], 0
        current.append(text)
        size += length
    if current:
        batches.append(current)
    return batches


class Summarizer:
    def __init__(self, config: Config, backend: Backend | None = None) -> None:
        self.config = config
        self._backend = backend

    @property
    def backend(self) -> Backend:
        if self._backend is None:
            self._backend = build_backend(self.config)
        return self._backend

    def summarize(
        self,
        messages: Sequence[Message],
        *,
        chat_label: str,
        window_description: str,
    ) -> str:
        if not messages:
            return f"Nothing to summarize in {chat_label} for {window_description}."

        budget = self.config.transcript_budget
        chunks = chunk_messages(messages, budget)

        if len(chunks) == 1:
            prompt = (
                f"Conversation: {chat_label}\n"
                f"Window: {window_description}\n"
                f"Messages: {len(messages)}\n\n"
                f"Transcript:\n{render_transcript(messages, budget)}\n\n"
                "Summarize this conversation."
            )
            return self.backend.complete(SYSTEM_PROMPT, prompt)

        log.info(
            "transcript exceeds the %d character budget; summarizing in %d chunks",
            budget,
            len(chunks),
        )
        partials = [
            self.backend.complete(
                SYSTEM_PROMPT,
                (
                    f"Conversation: {chat_label}\n"
                    f"Window: {window_description}\n"
                    f"This is part {index} of {len(chunks)} of one long conversation, "
                    "in chronological order.\n\n"
                    f"Transcript:\n{render_transcript(chunk, budget)}\n\n"
                    "Summarize only this part. Keep the details a final combined "
                    "summary would need: who said what, decisions, action items, "
                    "and unresolved questions."
                ),
            )
            for index, chunk in enumerate(chunks, start=1)
        ]
        return self._combine(partials, chat_label, window_description, budget)

    def _combine(
        self,
        partials: list[str],
        chat_label: str,
        window_description: str,
        budget: int,
    ) -> str:
        """Fold partial summaries down to one, in rounds if they don't fit."""
        for _ in range(MAX_REDUCE_ROUNDS):
            batches = _batch_texts(partials, budget)
            if len(batches) == 1:
                return self.backend.complete(
                    SYSTEM_PROMPT,
                    self._combine_prompt(batches[0], chat_label, window_description, final=True),
                )
            partials = [
                self.backend.complete(
                    SYSTEM_PROMPT,
                    self._combine_prompt(batch, chat_label, window_description, final=False),
                )
                for batch in batches
            ]
        # Four rounds without converging means the model is padding rather than
        # condensing; return what we have instead of looping forever.
        return "\n\n".join(partials)

    @staticmethod
    def _combine_prompt(
        parts: Sequence[str], chat_label: str, window_description: str, *, final: bool
    ) -> str:
        joined = "\n\n".join(
            f"--- part {i} ---\n{text}" for i, text in enumerate(parts, start=1)
        )
        closing = (
            "Combine these into one summary of the whole conversation."
            if final
            else "Combine these into one shorter summary, preserving specifics."
        )
        return (
            f"Conversation: {chat_label}\n"
            f"Window: {window_description}\n\n"
            f"Below are summaries of consecutive parts of this one conversation, "
            f"in chronological order.\n\n{joined}\n\n"
            f"{closing} Merge duplicates, keep every decision, action item and open "
            "question, and do not add anything the parts do not say."
        )


__all__ = [
    "SYSTEM_PROMPT",
    "SummarizationRefused",
    "Summarizer",
    "SummarizerError",
    "SummarizerUnavailable",
    "chunk_messages",
    "render_message",
    "render_transcript",
]
