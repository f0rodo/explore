"""Run a local model as a subprocess, one process per summary.

Unlike the HTTP backends this keeps nothing resident between summaries, which
is what you want on a phone: a 1B model at Q4 holds ~1 GB, and Android will
either kill the server or kill whatever else you were using. Here the weights
are mmapped for the length of one call and released afterwards.

Example (llama.cpp):

    EDGE_COMMAND='llama-cli -m ~/models/model.gguf -c 4096 -n 512 \\
                  --temp 0.2 -no-cnv --no-display-prompt -f /dev/stdin'
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess

from ..config import Config
from ..errors import SummarizerError, SummarizerUnavailable
from .edge import strip_reasoning

log = logging.getLogger(__name__)

# llama.cpp marks the end of a generation in its stdout.
_TRAILERS = ("[end of text]",)


class CommandBackend:
    name = "command"

    def __init__(self, config: Config) -> None:
        self.config = config
        if not config.edge_command.strip():
            raise ValueError(
                "SUMMARIZER_BACKEND=command needs EDGE_COMMAND, e.g. "
                "EDGE_COMMAND='llama-cli -m ~/models/model.gguf -c 4096 -n 512 "
                "-no-cnv --no-display-prompt -f /dev/stdin'"
            )
        # expanduser per-argument: shlex.split does not expand ~ itself.
        self.argv = [os.path.expanduser(arg) for arg in shlex.split(config.edge_command)]

    def describe(self) -> str:
        return f"command backend: {' '.join(self.argv)}"

    def _render(self, system: str, prompt: str) -> tuple[list[str], str | None]:
        """Return (argv, stdin). Placeholders win; otherwise the text goes to stdin."""
        if any("{prompt}" in arg or "{system}" in arg for arg in self.argv):
            argv = [
                arg.replace("{system}", system).replace("{prompt}", prompt)
                for arg in self.argv
            ]
            return argv, None
        return self.argv, f"{system}\n\n{prompt}\n"

    def complete(self, system: str, prompt: str) -> str:
        argv, stdin = self._render(system, prompt)
        try:
            finished = subprocess.run(
                argv,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=self.config.edge_timeout,
            )
        except FileNotFoundError as exc:
            raise SummarizerUnavailable(f"{argv[0]!r} is not on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise SummarizerUnavailable(
                f"{argv[0]} did not finish within {self.config.edge_timeout:.0f}s"
            ) from exc
        except OSError as exc:
            raise SummarizerUnavailable(f"could not run {argv[0]}: {exc}") from exc

        if finished.stderr:
            # llama.cpp reports load and timing stats on stderr; useful when
            # tuning a phone, noise otherwise.
            log.debug("%s stderr: %s", argv[0], finished.stderr.strip()[-2000:])
        if finished.returncode != 0:
            detail = (finished.stderr or finished.stdout or "").strip()[-500:]
            raise SummarizerError(f"{argv[0]} exited {finished.returncode}: {detail}")

        text = strip_reasoning(finished.stdout)
        for trailer in _TRAILERS:
            if text.endswith(trailer):
                text = text[: -len(trailer)].strip()
        if not text:
            raise SummarizerError(f"{argv[0]} produced no output")
        return text

    def check(self) -> str:
        reply = self.complete(
            "You are a test harness. Answer in five words or fewer.",
            "Reply with: ready.",
        )
        return f"{self.argv[0]} ran and answered ({len(reply)} characters)"
