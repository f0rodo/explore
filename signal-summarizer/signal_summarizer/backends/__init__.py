"""Summarization backends: a local (edge) model, or the Claude API.

A backend does one thing — turn a system prompt plus a user prompt into text.
All the prompt building, chunking and formatting lives in `summarizer.py`, so
every backend produces summaries in the same shape.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..config import Config
from ..errors import SummarizationRefused, SummarizerError, SummarizerUnavailable


@runtime_checkable
class Backend(Protocol):
    name: str

    def complete(self, system: str, prompt: str) -> str:
        """Return the model's answer, or raise a SummarizerError."""

    def describe(self) -> str:
        """One line naming the model and where it runs."""

    def check(self) -> str:
        """Verify the backend is reachable and usable; return a status line."""


def build_backend(config: Config) -> Backend:
    if config.backend == "claude":
        from .claude import ClaudeBackend

        return ClaudeBackend(config)
    if config.backend == "ollama":
        from .edge import OllamaBackend

        return OllamaBackend(config)
    if config.backend in {"openai", "openai-compatible", "llama.cpp"}:
        from .edge import OpenAICompatibleBackend

        return OpenAICompatibleBackend(config)
    if config.backend == "command":
        from .command import CommandBackend

        return CommandBackend(config)
    raise ValueError(
        f"unknown SUMMARIZER_BACKEND {config.backend!r} "
        "(expected: ollama, openai, command, or claude)"
    )


__all__ = [
    "Backend",
    "SummarizationRefused",
    "SummarizerError",
    "SummarizerUnavailable",
    "build_backend",
]
