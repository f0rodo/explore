"""The hosted backend: Anthropic's Messages API."""

from __future__ import annotations

from typing import Any

from ..config import Config
from ..errors import SummarizationRefused, SummarizerError

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeBackend:
    name = "claude"

    def __init__(self, config: Config, client: Any | None = None) -> None:
        self.config = config
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic  # imported lazily: the edge backends need no SDK

            self._client = anthropic.Anthropic()
        return self._client

    def describe(self) -> str:
        return f"Anthropic API, model {self.config.model} (effort {self.config.effort})"

    def complete(self, system: str, prompt: str) -> str:
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "system": system,
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
        if not text:
            raise SummarizerError("the model returned an empty summary")
        return text

    def check(self) -> str:
        response = self.client.messages.create(
            model=self.config.model,
            max_tokens=16,
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
        )
        return f"Anthropic API reachable, {getattr(response, 'model', self.config.model)} responded"
