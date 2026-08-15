from conftest import make_message


def test_add_is_idempotent_per_sender_and_timestamp(store):
    message = make_message()
    assert store.add(message) is True
    assert store.add(message) is False
    assert len(store.recent("group:abc=")) == 1


def test_recent_returns_oldest_first_and_caps_at_limit(store):
    for i in range(5):
        store.add(make_message(timestamp=1000 + i, body=f"m{i}"))
    recent = store.recent("group:abc=", limit=3)
    assert [m.body for m in recent] == ["m2", "m3", "m4"]


def test_recent_filters_by_since(store):
    store.add(make_message(timestamp=1_000, body="old"))
    store.add(make_message(timestamp=9_000, body="new"))
    assert [m.body for m in store.recent("group:abc=", since=5_000)] == ["new"]


def test_recent_scopes_to_one_chat(store):
    store.add(make_message(chat_id="group:abc=", body="group"))
    store.add(make_message(chat_id="dm:+15551111111", chat_kind="dm", body="dm"))
    assert [m.body for m in store.recent("dm:+15551111111")] == ["dm"]


def test_chats_lists_counts_newest_first(store):
    store.add(make_message(chat_id="group:abc=", timestamp=1_000))
    store.add(make_message(chat_id="group:abc=", timestamp=2_000))
    store.add(make_message(chat_id="dm:+1", chat_kind="dm", chat_label="Bob", timestamp=9_000))
    chats = store.chats()
    assert [c[0] for c in chats] == ["dm:+1", "group:abc="]
    assert chats[1][2] == 2


def test_label_for_uses_the_most_recent_label(store):
    store.add(make_message(timestamp=1_000, chat_label="Old name"))
    store.add(make_message(timestamp=2_000, chat_label="New name"))
    assert store.label_for("group:abc=") == "New name"
    assert store.label_for("group:missing") is None


def test_prune_drops_old_messages(store):
    store.add(make_message(timestamp=1_000, body="old"))
    store.add(make_message(timestamp=9_000, body="new"))
    assert store.prune(5_000) == 1
    assert [m.body for m in store.recent("group:abc=")] == ["new"]


def test_round_trip_preserves_fields(store):
    store.add(make_message(quote="Bob: hi", attachments=2, body="yes"))
    stored = store.recent("group:abc=")[0]
    assert (stored.quote, stored.attachments, stored.sender) == ("Bob: hi", 2, "Alice")
