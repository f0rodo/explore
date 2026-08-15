"""The subprocess backend — a local model with nothing resident between calls."""

import dataclasses
import os
import stat
import sys

import pytest

from signal_summarizer.backends import build_backend
from signal_summarizer.backends.command import CommandBackend
from signal_summarizer.errors import SummarizerError, SummarizerUnavailable


def fake_model(tmp_path, script: str) -> str:
    """Write a stand-in for llama-cli and return its path."""
    path = tmp_path / "fake-llama"
    path.write_text(f"#!{sys.executable}\n{script}")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


ECHO_STDIN = """
import sys
prompt = sys.stdin.read()
sys.stderr.write("llama_perf: 12 tokens/s\\n")
print("SUMMARY OF:", prompt.strip().splitlines()[-1])
print("[end of text]")
"""

ARGS_ONLY = """
import sys
print("got args:", " ".join(sys.argv[1:]))
"""


def command_config(config, command):
    return dataclasses.replace(config, backend="command", edge_command=command)


def test_prompt_is_piped_on_stdin(config, tmp_path):
    backend = CommandBackend(command_config(config, fake_model(tmp_path, ECHO_STDIN)))
    assert backend.complete("be brief", "summarize this") == "SUMMARY OF: summarize this"


def test_placeholders_are_substituted_when_present(config, tmp_path):
    script = fake_model(tmp_path, ARGS_ONLY)
    backend = CommandBackend(command_config(config, f"{script} --sys {{system}} -p {{prompt}}"))
    assert backend.complete("BRIEF", "SUMMARIZE") == "got args: --sys BRIEF -p SUMMARIZE"


def test_tilde_in_the_command_is_expanded(config, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    script = fake_model(tmp_path, ECHO_STDIN)
    backend = CommandBackend(
        command_config(config, f"~/{os.path.basename(script)} -m ~/models/x.gguf")
    )
    assert backend.argv[0] == script
    assert backend.argv[2] == str(tmp_path / "models/x.gguf")


def test_reasoning_blocks_and_end_marker_are_stripped(config, tmp_path):
    script = fake_model(
        tmp_path,
        "print('<think>weighing it up</think>\\nAlice moved standup.')\nprint('[end of text]')",
    )
    backend = CommandBackend(command_config(config, script))
    assert backend.complete("s", "p") == "Alice moved standup."


def test_nonzero_exit_is_an_error(config, tmp_path):
    script = fake_model(tmp_path, "import sys; sys.stderr.write('out of memory\\n'); sys.exit(1)")
    backend = CommandBackend(command_config(config, script))
    with pytest.raises(SummarizerError, match="out of memory"):
        backend.complete("s", "p")


def test_empty_output_is_an_error(config, tmp_path):
    backend = CommandBackend(command_config(config, fake_model(tmp_path, "pass")))
    with pytest.raises(SummarizerError, match="no output"):
        backend.complete("s", "p")


def test_missing_binary_is_unavailable(config):
    backend = CommandBackend(command_config(config, "/nonexistent/llama-cli -m x"))
    with pytest.raises(SummarizerUnavailable, match="not on PATH"):
        backend.complete("s", "p")


def test_a_hung_model_times_out(config, tmp_path):
    script = fake_model(tmp_path, "import time; time.sleep(30)")
    backend = dataclasses.replace(command_config(config, script), edge_timeout=1)
    with pytest.raises(SummarizerUnavailable, match="did not finish"):
        CommandBackend(backend).complete("s", "p")


def test_missing_command_is_a_configuration_error(config):
    with pytest.raises(ValueError, match="EDGE_COMMAND"):
        CommandBackend(dataclasses.replace(config, backend="command", edge_command=""))


def test_check_runs_the_model(config, tmp_path):
    backend = CommandBackend(command_config(config, fake_model(tmp_path, ECHO_STDIN)))
    assert "answered" in backend.check()


def test_build_backend_selects_it(config, tmp_path):
    backend = build_backend(command_config(config, fake_model(tmp_path, ECHO_STDIN)))
    assert isinstance(backend, CommandBackend)
