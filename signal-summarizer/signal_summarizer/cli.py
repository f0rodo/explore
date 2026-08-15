"""Command line entry points: run the bot, list chats, print a digest."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime

from .bot import HOUR_MS, SUMMARY_HEADER, SummarizerBot
from .config import Config
from .envelope import split_chat_id
from .signal_client import SignalClient
from .store import MessageStore
from .summarizer import Summarizer


def _now_ms() -> int:
    return int(time.time() * 1000)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signal-summarizer", description="A Signal bot that summarizes chats."
    )
    parser.add_argument(
        "--log-level", default="INFO", help="DEBUG, INFO, WARNING, ERROR (default: INFO)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="listen for messages and answer summary commands")
    sub.add_parser("chats", help="list the chats with recorded history")

    digest = sub.add_parser(
        "digest", help="summarize a chat once (for cron-driven daily digests)"
    )
    digest.add_argument("--chat", required=True, help="chat id, e.g. group:abc123= or dm:+15551234567")
    digest.add_argument("--hours", type=int, help="window in hours (default: config value)")
    digest.add_argument("--messages", type=int, help="summarize the last N messages instead")
    digest.add_argument(
        "--send", action="store_true", help="post the summary back to the chat"
    )
    return parser


def cmd_run(config: Config) -> int:
    client = SignalClient(
        config.rpc_address, account=config.account, request_timeout=config.request_timeout
    )
    with MessageStore(config.db_path) as store:
        bot = SummarizerBot(config, client, store, Summarizer(config))
        logging.info("listening for messages on %s as %s", config.rpc_address, config.account)
        bot.run()
    return 0


def cmd_chats(config: Config) -> int:
    with MessageStore(config.db_path) as store:
        rows = store.chats()
    if not rows:
        print("No messages recorded yet.")
        return 0
    width = max(len(chat_id) for chat_id, _, _, _ in rows)
    for chat_id, label, count, last in rows:
        seen = datetime.fromtimestamp(last / 1000).astimezone().strftime("%Y-%m-%d %H:%M")
        print(f"{chat_id:<{width}}  {count:>6} msgs  last {seen}  {label}")
    return 0


def cmd_digest(config: Config, args: argparse.Namespace) -> int:
    try:
        kind, identifier = split_chat_id(args.chat)  # fail early on a bad chat id
    except ValueError as exc:
        print(f"{exc} (run `chats` to see the ids I know about)", file=sys.stderr)
        return 2

    with MessageStore(config.db_path) as store:
        if args.messages:
            since, limit = None, args.messages
            plural = "" if args.messages == 1 else "s"
            description = f"the last {args.messages} message{plural}"
        else:
            hours = args.hours or config.default_window_hours
            since, limit = _now_ms() - hours * HOUR_MS, config.max_messages
            plural = "" if hours == 1 else "s"
            description = f"the last {hours} hour{plural}"

        history = store.recent(args.chat, since=since, limit=limit)
        label = store.label_for(args.chat) or args.chat
        if not history:
            print(f"No messages recorded for {args.chat} in {description}.", file=sys.stderr)
            return 1

        summary = Summarizer(config).summarize(
            history, chat_label=label, window_description=description
        )

    header = f"{SUMMARY_HEADER} of {label} - {description} ({len(history)} messages)"
    body = f"{header}\n\n{summary}"
    print(body)

    if args.send:
        client = SignalClient(
            config.rpc_address, account=config.account, request_timeout=config.request_timeout
        )
        with client:
            if kind == "group":
                client.send_message(body, group_id=identifier)
            else:
                client.send_message(body, recipient=identifier)
        logging.info("digest sent to %s", args.chat)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    try:
        config = Config.from_env()
    except ValueError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    if args.command == "run":
        return cmd_run(config)
    if args.command == "chats":
        return cmd_chats(config)
    if args.command == "digest":
        return cmd_digest(config, args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
