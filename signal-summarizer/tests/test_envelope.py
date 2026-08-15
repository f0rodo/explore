from signal_summarizer.envelope import dm_chat_id, group_chat_id, parse_envelope, split_chat_id

ACCOUNT = "+15550000000"


def receive(envelope):
    return {"envelope": envelope, "account": ACCOUNT}


def test_group_message():
    message = parse_envelope(
        receive(
            {
                "source": "+15551111111",
                "sourceNumber": "+15551111111",
                "sourceName": "Alice",
                "timestamp": 1_700_000_000_000,
                "dataMessage": {
                    "timestamp": 1_700_000_000_000,
                    "message": "ship it friday",
                    "attachments": [],
                    "groupInfo": {"groupId": "Zm9vYmFy", "groupName": "Launch", "type": "DELIVER"},
                },
            }
        ),
        ACCOUNT,
    )
    assert message is not None
    assert message.chat_id == group_chat_id("Zm9vYmFy")
    assert message.chat_kind == "group"
    assert message.chat_label == "Launch"
    assert message.sender == "Alice"
    assert message.body == "ship it friday"
    assert message.from_self is False


def test_direct_message_falls_back_to_number_when_unnamed():
    message = parse_envelope(
        receive(
            {
                "sourceNumber": "+15552222222",
                "timestamp": 1_700_000_001_000,
                "dataMessage": {"message": "hey", "timestamp": 1_700_000_001_000},
            }
        ),
        ACCOUNT,
    )
    assert message is not None
    assert message.chat_id == dm_chat_id("+15552222222")
    assert message.sender == "+15552222222"
    assert message.chat_label == "+15552222222"


def test_sync_sent_message_is_attributed_to_self():
    message = parse_envelope(
        receive(
            {
                "sourceNumber": ACCOUNT,
                "timestamp": 1_700_000_002_000,
                "syncMessage": {
                    "sentMessage": {
                        "timestamp": 1_700_000_002_000,
                        "message": "on my way",
                        "destinationNumber": "+15553333333",
                    }
                },
            }
        ),
        ACCOUNT,
    )
    assert message is not None
    assert message.from_self is True
    assert message.sender == "you"
    assert message.sender_id == ACCOUNT
    assert message.chat_id == dm_chat_id("+15553333333")


def test_quote_is_captured():
    message = parse_envelope(
        receive(
            {
                "sourceNumber": "+15551111111",
                "sourceName": "Alice",
                "timestamp": 5,
                "dataMessage": {
                    "message": "agreed",
                    "timestamp": 5,
                    "quote": {"authorName": "Bob", "text": "thursday works"},
                },
            }
        ),
        ACCOUNT,
    )
    assert message is not None
    assert message.quote == "Bob: thursday works"


def test_attachment_only_message_is_kept():
    message = parse_envelope(
        receive(
            {
                "sourceNumber": "+15551111111",
                "sourceName": "Alice",
                "timestamp": 7,
                "dataMessage": {
                    "message": None,
                    "timestamp": 7,
                    "attachments": [{"id": "1"}, {"id": "2"}],
                },
            }
        ),
        ACCOUNT,
    )
    assert message is not None
    assert message.body == ""
    assert message.attachments == 2


def test_non_chat_envelopes_are_ignored():
    ignored = [
        {"receiptMessage": {"when": 1}},
        {"typingMessage": {"action": "STARTED"}},
        {"dataMessage": {"reaction": {"emoji": "\U0001f44d"}, "timestamp": 1}},
        {"dataMessage": {"remoteDelete": {"timestamp": 1}}},
        {"dataMessage": {"message": "", "attachments": []}},
    ]
    for envelope in ignored:
        assert parse_envelope(receive(envelope), ACCOUNT) is None
    assert parse_envelope({}, ACCOUNT) is None


def test_split_chat_id_roundtrip():
    assert split_chat_id(group_chat_id("abc=")) == ("group", "abc=")
    assert split_chat_id(dm_chat_id("+15551111111")) == ("dm", "+15551111111")
