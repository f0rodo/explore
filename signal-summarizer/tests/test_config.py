import pytest

from signal_summarizer.config import Config

BASE = {"SIGNAL_ACCOUNT": "+15551234567"}


def test_defaults_to_a_local_model():
    config = Config.from_env(BASE)
    assert config.backend == "ollama"
    assert config.is_edge is True
    assert config.edge_endpoint == "http://127.0.0.1:11434"
    assert config.edge_model == "llama3.2:3b"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("ollama", "ollama"),
        ("OLLAMA", "ollama"),
        ("llama.cpp", "openai"),
        ("lmstudio", "openai"),
        ("vllm", "openai"),
        ("openai-compatible", "openai"),
        ("claude", "claude"),
        ("anthropic", "claude"),
    ],
)
def test_backend_aliases(value, expected):
    assert Config.from_env({**BASE, "SUMMARIZER_BACKEND": value}).backend == expected


def test_unknown_backend_is_rejected():
    with pytest.raises(ValueError, match="SUMMARIZER_BACKEND"):
        Config.from_env({**BASE, "SUMMARIZER_BACKEND": "gpt4all"})


def test_openai_backend_defaults_to_the_llama_cpp_port():
    config = Config.from_env({**BASE, "SUMMARIZER_BACKEND": "llama.cpp"})
    assert config.edge_endpoint == "http://127.0.0.1:8080/v1"


def test_ollama_host_is_honoured_and_gets_a_scheme():
    config = Config.from_env({**BASE, "OLLAMA_HOST": "192.168.1.10:11434"})
    assert config.edge_endpoint == "http://192.168.1.10:11434"


def test_edge_endpoint_wins_over_ollama_host():
    config = Config.from_env(
        {**BASE, "OLLAMA_HOST": "1.2.3.4:11434", "EDGE_ENDPOINT": "http://box:9999"}
    )
    assert config.edge_endpoint == "http://box:9999"


def test_transcript_budget_is_derived_from_the_context_window():
    config = Config.from_env({**BASE, "EDGE_CONTEXT_TOKENS": "8192"})
    assert 15_000 < config.transcript_budget < 28_000


def test_transcript_budget_can_be_set_explicitly():
    config = Config.from_env({**BASE, "SUMMARIZER_MAX_CHARS": "1234"})
    assert config.transcript_budget == 1234


def test_claude_backend_gets_the_large_budget():
    config = Config.from_env({**BASE, "SUMMARIZER_BACKEND": "claude"})
    assert config.transcript_budget == 60_000
    assert config.is_edge is False


def test_tiny_context_windows_still_leave_room_to_write():
    config = Config.from_env(
        {**BASE, "EDGE_CONTEXT_TOKENS": "1024", "EDGE_OUTPUT_TOKENS": "512"}
    )
    assert config.transcript_budget > 0


def test_numeric_settings_are_validated():
    with pytest.raises(ValueError, match="EDGE_CONTEXT_TOKENS"):
        Config.from_env({**BASE, "EDGE_CONTEXT_TOKENS": "lots"})
    with pytest.raises(ValueError, match="EDGE_TEMPERATURE"):
        Config.from_env({**BASE, "EDGE_TEMPERATURE": "warm"})
