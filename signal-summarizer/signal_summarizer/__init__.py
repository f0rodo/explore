"""A Signal bot that summarizes chat history with the Claude API."""

from .bot import SummarizerBot, parse_window
from .config import Config
from .envelope import Message, parse_envelope
from .signal_client import SignalClient, SignalRpcError
from .store import MessageStore
from .summarizer import SummarizationRefused, Summarizer

__all__ = [
    "Config",
    "Message",
    "MessageStore",
    "SignalClient",
    "SignalRpcError",
    "SummarizationRefused",
    "Summarizer",
    "SummarizerBot",
    "parse_envelope",
    "parse_window",
]
