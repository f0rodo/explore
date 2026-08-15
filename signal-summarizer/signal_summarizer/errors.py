"""Errors the bot is willing to explain in chat."""

from __future__ import annotations


class SummarizerError(RuntimeError):
    """Something went wrong producing a summary, with a message worth showing."""


class SummarizerUnavailable(SummarizerError):
    """The model backend is unreachable or misconfigured."""


class SummarizationRefused(SummarizerError):
    """The model declined to summarize this transcript."""
