# signal-summarizer

A Signal bot that catches you up on a chat. Ask it in the conversation:

```
!summarize          # the last 24 hours
!summarize 90m      # the last 90 minutes
!summarize 7d       # the last 7 days
!summarize 50       # the last 50 messages
!help
```

It replies in the same chat with a plain-text summary: what was discussed,
what was decided, who owes what, and what is still open. Summaries are
generated with the Claude API (`claude-opus-5` by default).

It works in group chats and in direct messages, keeps its own history in a
local SQLite file, and can also post a scheduled digest from cron.

## How it fits together

```
Signal ──► signal-cli daemon ──JSON-RPC──► signal-summarizer ──► SQLite
                    ▲                             │
                    └──────── summary ────────────┴──► Claude API
```

`signal-cli` owns the Signal protocol, registration, and encryption. This bot
talks to its JSON-RPC socket: it records incoming messages and, when someone
runs a command, sends the recent transcript to Claude and posts the reply.

**It can only summarize messages it saw while running.** Signal has no server
side history to backfill from.

## Setup

### 1. Install and link signal-cli

Install [signal-cli](https://github.com/AsamK/signal-cli) (`brew install
signal-cli`, or the release tarball on Linux), then either link the bot to an
existing Signal account as a second device:

```bash
signal-cli link -n "summarizer-bot"   # prints a tsdevice:/ URI — turn it into a
                                      # QR code and scan it from Signal ▸ Linked devices
```

…or register a separate number for the bot:

```bash
signal-cli -a +15551234567 register       # add --voice if SMS does not arrive
signal-cli -a +15551234567 verify 123456
```

A linked device sees the chats of the account it is linked to, which is
usually what you want for summarizing your own group chats.

### 2. Start the daemon

```bash
signal-cli -a +15551234567 daemon --tcp 127.0.0.1:7583
```

A UNIX socket works too (`--socket /run/signal-cli.sock`); point
`SIGNAL_RPC_ADDRESS` at `unix:///run/signal-cli.sock`. Keep the socket bound
to localhost — anyone who can reach it can send messages as your account.

### 3. Install the bot

```bash
cd signal-summarizer
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### 4. Configure

```bash
export ANTHROPIC_API_KEY=sk-ant-...       # or run `ant auth login`
export SIGNAL_ACCOUNT=+15551234567
export SIGNAL_RPC_ADDRESS=tcp://127.0.0.1:7583
export SUMMARIZER_DB=~/.local/share/signal-summarizer.db
```

### 5. Run

```bash
signal-summarizer run          # or: python -m signal_summarizer run
```

Send `!help` in any chat the account is in to confirm it is alive.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `SIGNAL_ACCOUNT` | *(required)* | The number signal-cli is registered or linked to |
| `SIGNAL_RPC_ADDRESS` | `tcp://127.0.0.1:7583` | `tcp://host:port` or `unix:///path/to.sock` |
| `SUMMARIZER_DB` | `signal-summarizer.db` | SQLite file for message history |
| `SUMMARIZER_PREFIX` | `!` | Command prefix |
| `SUMMARIZER_WINDOW_HOURS` | `24` | Default window for a bare `!summarize` |
| `SUMMARIZER_MAX_MESSAGES` | `400` | Cap on messages fed to one summary |
| `SUMMARIZER_MAX_CHARS` | `60000` | Cap on transcript characters; oldest lines are dropped first |
| `SUMMARIZER_RETENTION_DAYS` | `30` | History older than this is deleted |
| `SUMMARIZER_ALLOWED_CHATS` | *(all)* | Comma-separated chat ids to restrict the bot to |
| `ANTHROPIC_API_KEY` | *(from SDK auth)* | API key, if you are not using an `ant auth login` profile |
| `ANTHROPIC_MODEL` | `claude-opus-5` | Model id |
| `ANTHROPIC_EFFORT` | `medium` | `low`, `medium`, `high`, `xhigh`, or `max` |
| `ANTHROPIC_MAX_TOKENS` | `4000` | Output cap for a summary |
| `ANTHROPIC_FALLBACKS` | `1` | Server-side refusal fallbacks (see below); `0` disables |

Chat ids look like `group:<base64 group id>` or `dm:+15551234567`. Run
`signal-summarizer chats` to list the ones it has seen.

## Scheduled digests

`digest` summarizes one chat and prints it; `--send` posts it back to the chat.
For a 08:00 weekday digest:

```cron
0 8 * * 1-5 cd /opt/signal-summarizer && .venv/bin/signal-summarizer digest \
    --chat 'group:Zm9vYmFy' --hours 24 --send >> /var/log/signal-digest.log 2>&1
```

The cron job needs the same environment variables as `run` and can share the
same database file while the bot is running.

## Running as a service

```ini
# /etc/systemd/system/signal-summarizer.service
[Unit]
Description=Signal chat summarizer
After=network-online.target signal-cli.service
Requires=signal-cli.service

[Service]
Type=simple
User=signal
WorkingDirectory=/opt/signal-summarizer
EnvironmentFile=/etc/signal-summarizer.env
ExecStart=/opt/signal-summarizer/.venv/bin/signal-summarizer run
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## Notes on behavior

- **Privacy.** Message bodies are stored in plaintext in the SQLite file and
  the transcript for the requested window is sent to the Anthropic API when
  someone asks for a summary. Keep the database on an encrypted disk with
  restrictive permissions, set `SUMMARIZER_RETENTION_DAYS` to the shortest
  window you can live with, and tell the group the bot is there.
- **Scope.** `SUMMARIZER_ALLOWED_CHATS` restricts both recording and commands
  to specific chats. Anyone in an allowed chat can run `!summarize` for that
  chat only.
- **Refusals.** Claude Opus 5's safety classifiers can decline a request. The
  bot enables server-side fallbacks (`fallbacks: "default"`), so a declined
  request is retried on Anthropic's recommended fallback model; if that also
  declines, the bot says so in the chat rather than failing silently. Set
  `ANTHROPIC_FALLBACKS=0` to turn that off.
- **Cost.** Each summary is one API call over at most `SUMMARIZER_MAX_CHARS` of
  transcript. Lower `ANTHROPIC_EFFORT` to `low` for cheaper, terser summaries.
- **Reconnects.** If signal-cli restarts, the bot reconnects with exponential
  backoff (up to 60s) and keeps its history.

## Development

```bash
python -m pytest
```

The tests cover envelope parsing, the message store, command handling, the
JSON-RPC client (against a stub daemon on a real socket), and the CLI. No
network access or API key is required to run them.

## Layout

| File | Purpose |
| --- | --- |
| `signal_summarizer/signal_client.py` | JSON-RPC client for the signal-cli daemon |
| `signal_summarizer/envelope.py` | signal-cli envelopes → flat message records |
| `signal_summarizer/store.py` | SQLite history |
| `signal_summarizer/summarizer.py` | Prompt construction and the Claude API call |
| `signal_summarizer/bot.py` | Commands, replies, and the listen loop |
| `signal_summarizer/cli.py` | `run`, `chats`, `digest` |
