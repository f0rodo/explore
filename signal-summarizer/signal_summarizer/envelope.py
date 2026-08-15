"""Turn signal-cli envelopes into the flat records we store."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

GROUP_PREFIX = "group:"
DM_PREFIX = "dm:"


def group_chat_id(group_id: str) -> str:
    return f"{GROUP_PREFIX}{group_id}"


def dm_chat_id(recipient: str) -> str:
    return f"{DM_PREFIX}{recipient}"


def split_chat_id(chat_id: str) -> tuple[str, str]:
    """Return ("group"|"dm", identifier) for a chat id."""
    if chat_id.startswith(GROUP_PREFIX):
        return "group", chat_id[len(GROUP_PREFIX) :]
    if chat_id.startswith(DM_PREFIX):
        return "dm", chat_id[len(DM_PREFIX) :]
    raise ValueError(f"unrecognized chat id: {chat_id!r}")


@dataclass(frozen=True)
class Message:
    chat_id: str
    chat_kind: str  # "group" or "dm"
    chat_label: str  # group name or contact name, best effort
    sender: str  # display name if signal-cli knows one, else the number
    sender_id: str  # number or uuid
    timestamp: int  # epoch milliseconds
    body: str
    quote: str | None = None
    attachments: int = 0
    from_self: bool = False


def _sender_id(envelope: Mapping[str, Any]) -> str:
    for key in ("sourceNumber", "sourceUuid", "source"):
        value = envelope.get(key)
        if value:
            return str(value)
    return "unknown"


def _sender_name(envelope: Mapping[str, Any], sender_id: str) -> str:
    name = envelope.get("sourceName")
    return str(name) if name else sender_id


def parse_envelope(params: Mapping[str, Any], account: str) -> Message | None:
    """Extract a storable message from a `receive` notification.

    Returns None for anything that is not chat text: receipts, typing
    indicators, reactions, group updates, and so on.
    """
    envelope = params.get("envelope")
    if not isinstance(envelope, Mapping):
        return None

    sync_sent = None
    sync = envelope.get("syncMessage")
    if isinstance(sync, Mapping):
        candidate = sync.get("sentMessage")
        if isinstance(candidate, Mapping):
            sync_sent = candidate

    data = envelope.get("dataMessage")
    if not isinstance(data, Mapping):
        data = sync_sent
    if not isinstance(data, Mapping):
        return None

    # Reactions, remote deletes and expiry-timer changes ride along on a
    # dataMessage but carry no conversation content of their own.
    if data.get("reaction") or data.get("remoteDelete"):
        return None

    body = data.get("message") or ""
    attachments = data.get("attachments") or []
    if not isinstance(attachments, list):
        attachments = []
    if not body and not attachments:
        return None

    from_self = sync_sent is not None
    group_info = data.get("groupInfo")
    if isinstance(group_info, Mapping) and group_info.get("groupId"):
        chat_id = group_chat_id(str(group_info["groupId"]))
        chat_kind = "group"
        chat_label = str(group_info.get("groupName") or group_info["groupId"])
    elif from_self:
        destination = (
            data.get("destinationNumber")
            or data.get("destination")
            or data.get("destinationUuid")
        )
        if not destination:
            return None
        chat_id = dm_chat_id(str(destination))
        chat_kind = "dm"
        chat_label = str(data.get("destinationName") or destination)
    else:
        sender_id = _sender_id(envelope)
        chat_id = dm_chat_id(sender_id)
        chat_kind = "dm"
        chat_label = _sender_name(envelope, sender_id)

    if from_self:
        sender_id = account
        sender = "you"
    else:
        sender_id = _sender_id(envelope)
        sender = _sender_name(envelope, sender_id)

    timestamp = data.get("timestamp") or envelope.get("timestamp") or 0

    quote = None
    quote_info = data.get("quote")
    if isinstance(quote_info, Mapping):
        quoted_text = quote_info.get("text")
        quoted_author = quote_info.get("authorName") or quote_info.get("author")
        if quoted_text:
            prefix = f"{quoted_author}: " if quoted_author else ""
            quote = f"{prefix}{quoted_text}"

    return Message(
        chat_id=chat_id,
        chat_kind=chat_kind,
        chat_label=chat_label,
        sender=sender,
        sender_id=str(sender_id),
        timestamp=int(timestamp),
        body=str(body),
        quote=quote,
        attachments=len(attachments),
        from_self=from_self,
    )
