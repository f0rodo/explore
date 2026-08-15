"""SQLite-backed message history."""

from __future__ import annotations

import sqlite3
from typing import Iterable, Sequence

from .envelope import Message

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     TEXT    NOT NULL,
    chat_kind   TEXT    NOT NULL,
    chat_label  TEXT    NOT NULL,
    sender      TEXT    NOT NULL,
    sender_id   TEXT    NOT NULL,
    timestamp   INTEGER NOT NULL,
    body        TEXT    NOT NULL,
    quote       TEXT,
    attachments INTEGER NOT NULL DEFAULT 0,
    UNIQUE (chat_id, sender_id, timestamp)
);
CREATE INDEX IF NOT EXISTS messages_chat_time ON messages (chat_id, timestamp);
"""


class MessageStore:
    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "MessageStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def add(self, message: Message) -> bool:
        """Store a message. Returns False if it was already recorded."""
        cur = self._conn.execute(
            """
            INSERT OR IGNORE INTO messages
                (chat_id, chat_kind, chat_label, sender, sender_id,
                 timestamp, body, quote, attachments)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message.chat_id,
                message.chat_kind,
                message.chat_label,
                message.sender,
                message.sender_id,
                message.timestamp,
                message.body,
                message.quote,
                message.attachments,
            ),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def recent(
        self,
        chat_id: str,
        *,
        since: int | None = None,
        limit: int = 400,
    ) -> list[Message]:
        """Messages in a chat, oldest first, capped at the `limit` newest."""
        sql = "SELECT * FROM messages WHERE chat_id = ?"
        args: list[object] = [chat_id]
        if since is not None:
            sql += " AND timestamp >= ?"
            args.append(since)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        args.append(limit)
        rows = self._conn.execute(sql, args).fetchall()
        return [_row_to_message(row) for row in reversed(rows)]

    def chats(self) -> list[tuple[str, str, int, int]]:
        """(chat_id, chat_label, message_count, last_timestamp), newest first."""
        rows = self._conn.execute(
            """
            SELECT chat_id,
                   chat_label,
                   COUNT(*)       AS message_count,
                   MAX(timestamp) AS last_timestamp
            FROM messages
            GROUP BY chat_id
            ORDER BY last_timestamp DESC
            """
        ).fetchall()
        return [
            (r["chat_id"], r["chat_label"], r["message_count"], r["last_timestamp"])
            for r in rows
        ]

    def label_for(self, chat_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT chat_label FROM messages WHERE chat_id = ? "
            "ORDER BY timestamp DESC LIMIT 1",
            (chat_id,),
        ).fetchone()
        return row["chat_label"] if row else None

    def prune(self, older_than: int) -> int:
        """Delete messages older than an epoch-millisecond cutoff."""
        cur = self._conn.execute(
            "DELETE FROM messages WHERE timestamp < ?", (older_than,)
        )
        self._conn.commit()
        return cur.rowcount

    def add_all(self, messages: Iterable[Message]) -> int:
        return sum(1 for m in messages if self.add(m))


def _row_to_message(row: sqlite3.Row) -> Message:
    return Message(
        chat_id=row["chat_id"],
        chat_kind=row["chat_kind"],
        chat_label=row["chat_label"],
        sender=row["sender"],
        sender_id=row["sender_id"],
        timestamp=row["timestamp"],
        body=row["body"],
        quote=row["quote"],
        attachments=row["attachments"],
    )


__all__: Sequence[str] = ["MessageStore", "SCHEMA"]
