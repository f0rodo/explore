import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from signal_summarizer.config import Config  # noqa: E402
from signal_summarizer.envelope import Message  # noqa: E402
from signal_summarizer.store import MessageStore  # noqa: E402

ACCOUNT = "+15550000000"


@pytest.fixture
def config():
    return Config(account=ACCOUNT, db_path=":memory:", use_fallbacks=False)


@pytest.fixture
def store():
    with MessageStore(":memory:") as store:
        yield store


def make_message(**overrides) -> Message:
    defaults = dict(
        chat_id="group:abc=",
        chat_kind="group",
        chat_label="Launch",
        sender="Alice",
        sender_id="+15551111111",
        timestamp=1_700_000_000_000,
        body="hello",
        quote=None,
        attachments=0,
        from_self=False,
    )
    defaults.update(overrides)
    return Message(**defaults)
