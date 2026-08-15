"""A Signal bot that summarizes chat history with the Claude API."""

from .backends import build_backend
from .bot import SummarizerBot, parse_window
from .config import Config
from .envelope import Message, parse_envelope
from .errors import SummarizationRefused, SummarizerError, SummarizerUnavailable
from .signal_client import SignalClient, SignalRpcError
from .store import MessageStore
from .summarizer import Summarizer

__all__ = [
    "Config",
    "Message",
    "MessageStore",
    "SignalClient",
    "SignalRpcError",
    "SummarizationRefused",
    "Summarizer",
    "SummarizerBot",
    "SummarizerError",
    "SummarizerUnavailable",
    "build_backend",
    "parse_envelope",
    "parse_window",
]
