"""Local model backends: Ollama, and any OpenAI-compatible server.

Both talk plain HTTP over the loopback interface, so nothing here needs a
third-party client library and no message text leaves the machine.
"""

from __future__ import annotations

import json
import logging
import re
import socket
import urllib.error
import urllib.request
from typing import Any

from ..config import Config
from ..errors import SummarizerError, SummarizerUnavailable

log = logging.getLogger(__name__)

# Reasoning models (qwen3, deepseek-r1, ...) wrap their scratchpad in tags and
# expect the caller to drop it before showing the answer to anyone.
_REASONING_BLOCK = re.compile(
    r"<(think|thinking|reasoning)>.*?</\1>\s*", re.DOTALL | re.IGNORECASE
)


def strip_reasoning(text: str) -> str:
    cleaned = _REASONING_BLOCK.sub("", text)
    # An unclosed opening tag means the model ran out of tokens mid-thought.
    unclosed = re.search(r"<(think|thinking|reasoning)>", cleaned, re.IGNORECASE)
    if unclosed:
        cleaned = cleaned[: unclosed.start()]
    return cleaned.strip()


def _post_json(url: str, payload: dict, *, timeout: float, headers: dict | None = None) -> Any:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json", **(headers or {})}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500].strip()
        raise SummarizerError(f"{url} returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SummarizerUnavailable(f"could not reach {url}: {exc.reason}") from exc
    except socket.timeout as exc:
        raise SummarizerUnavailable(
            f"{url} did not answer within {timeout:.0f}s — a bigger model or a "
            "longer window may need more time than that"
        ) from exc
    except json.JSONDecodeError as exc:
        raise SummarizerError(f"{url} returned a response that was not JSON") from exc


def _get_json(url: str, *, timeout: float, headers: dict | None = None) -> Any:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise SummarizerError(f"{url} returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, socket.timeout) as exc:
        reason = getattr(exc, "reason", exc)
        raise SummarizerUnavailable(f"could not reach {url}: {reason}") from exc
    except json.JSONDecodeError as exc:
        raise SummarizerError(f"{url} returned a response that was not JSON") from exc


class OllamaBackend:
    """Talks to `ollama serve` (default http://127.0.0.1:11434)."""

    name = "ollama"

    def __init__(self, config: Config) -> None:
        self.config = config
        self.endpoint = config.edge_endpoint.rstrip("/")
        self.model = config.edge_model

    def describe(self) -> str:
        return f"ollama {self.model} at {self.endpoint}"

    def complete(self, system: str, prompt: str) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "options": {
                # num_ctx matters: Ollama silently truncates the prompt to the
                # model's default context otherwise, and the summary quietly
                # loses the oldest part of the conversation.
                "num_ctx": self.config.edge_context_tokens,
                "num_predict": self.config.edge_output_tokens,
                "temperature": self.config.edge_temperature,
            },
        }
        data = _post_json(
            f"{self.endpoint}/api/chat", payload, timeout=self.config.edge_timeout
        )
        if isinstance(data, dict) and data.get("error"):
            raise SummarizerError(f"ollama: {data['error']}")
        content = (((data or {}).get("message")) or {}).get("content", "")
        text = strip_reasoning(str(content))
        if not text:
            raise SummarizerError(f"ollama returned an empty summary for {self.model}")
        return text

    def check(self) -> str:
        data = _get_json(f"{self.endpoint}/api/tags", timeout=10.0)
        installed = [m.get("name", "") for m in (data or {}).get("models", [])]
        # Ollama reports "llama3.2:3b"; users often configure the bare "llama3.2".
        if not any(
            name == self.model or name.split(":")[0] == self.model.split(":")[0]
            for name in installed
        ):
            raise SummarizerUnavailable(
                f"ollama is running but {self.model!r} is not installed — "
                f"try `ollama pull {self.model}`. Installed: {', '.join(installed) or 'none'}"
            )
        return f"ollama reachable at {self.endpoint}, model {self.model} installed"


class OpenAICompatibleBackend:
    """Talks to llama.cpp's llama-server, LM Studio, vLLM, llamafile, ...

    Anything exposing POST {endpoint}/chat/completions works.
    """

    name = "openai"

    def __init__(self, config: Config) -> None:
        self.config = config
        self.endpoint = config.edge_endpoint.rstrip("/")
        self.model = config.edge_model

    def _headers(self) -> dict:
        if self.config.edge_api_key:
            return {"Authorization": f"Bearer {self.config.edge_api_key}"}
        return {}

    def describe(self) -> str:
        return f"openai-compatible {self.model} at {self.endpoint}"

    def complete(self, system: str, prompt: str) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            "temperature": self.config.edge_temperature,
            "max_tokens": self.config.edge_output_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        data = _post_json(
            f"{self.endpoint}/chat/completions",
            payload,
            timeout=self.config.edge_timeout,
            headers=self._headers(),
        )
        choices = (data or {}).get("choices") or []
        if not choices:
            error = (data or {}).get("error")
            raise SummarizerError(f"no completion returned: {error or data}")
        message = choices[0].get("message") or {}
        text = strip_reasoning(str(message.get("content") or ""))
        if not text:
            raise SummarizerError(f"{self.endpoint} returned an empty summary")
        return text

    def check(self) -> str:
        data = _get_json(f"{self.endpoint}/models", timeout=10.0, headers=self._headers())
        served = [m.get("id", "") for m in (data or {}).get("data", [])]
        if served and self.model not in served:
            log.warning(
                "%s serves %s, not %r — sending the request anyway, since many "
                "servers ignore the model field",
                self.endpoint,
                ", ".join(served),
                self.model,
            )
        return f"openai-compatible server reachable at {self.endpoint} (serving: {', '.join(served) or 'unreported'})"
