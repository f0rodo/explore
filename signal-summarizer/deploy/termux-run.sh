#!/data/data/com.termux/files/usr/bin/bash
#
# Start everything the bot needs on the phone, and keep it running:
#
#   signal-cli daemon   (in proot Debian, JSON-RPC on 127.0.0.1:7583)
#   signal-summarizer   (in Termux, connects to both of the above)
#
# The model is not started here: the phone profile runs llama-cli per summary
# so no weights sit in RAM between requests. Set SUMMARIZER_BACKEND=openai in
# the env file if you would rather keep llama-server resident.
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ENV_FILE="${ENV_FILE:-$HOME/signal-summarizer.env}"
LOG_DIR="${LOG_DIR:-$HOME/.signal-summarizer}"
RPC_PORT="${RPC_PORT:-7583}"

mkdir -p "$LOG_DIR"
[ -f "$ENV_FILE" ] || { echo "no env file at $ENV_FILE (copy deploy/phone.env)"; exit 1; }
set -a
# shellcheck source=/dev/null
. "$ENV_FILE"
set +a
[ -n "${SIGNAL_ACCOUNT:-}" ] || { echo "set SIGNAL_ACCOUNT in $ENV_FILE"; exit 1; }

# Keep the CPU awake; without this Android suspends the process and messages
# only arrive in bursts when the screen comes on.
command -v termux-wake-lock >/dev/null && termux-wake-lock || \
    echo "note: termux-api not installed, no wake lock"

cleanup() {
    echo "stopping..."
    [ -n "${DAEMON_PID:-}" ] && kill "$DAEMON_PID" 2>/dev/null || true
    proot-distro login debian -- pkill -f 'signal-cli.*daemon' 2>/dev/null || true
    command -v termux-wake-unlock >/dev/null && termux-wake-unlock || true
}
trap cleanup EXIT INT TERM

echo ">> starting signal-cli daemon for $SIGNAL_ACCOUNT"
proot-distro login debian -- \
    signal-cli -a "$SIGNAL_ACCOUNT" daemon --tcp "127.0.0.1:$RPC_PORT" \
    >>"$LOG_DIR/signal-cli.log" 2>&1 &
DAEMON_PID=$!

for _ in $(seq 1 30); do
    if timeout 1 bash -c "</dev/tcp/127.0.0.1/$RPC_PORT" 2>/dev/null; then break; fi
    sleep 1
done
timeout 1 bash -c "</dev/tcp/127.0.0.1/$RPC_PORT" 2>/dev/null || {
    echo "signal-cli never opened port $RPC_PORT — see $LOG_DIR/signal-cli.log"
    tail -20 "$LOG_DIR/signal-cli.log" || true
    exit 1
}

echo ">> checking the model"
"$PROJECT_DIR/.venv/bin/signal-summarizer" check || {
    echo "the model backend is not working; fix that before running the bot"
    exit 1
}

echo ">> starting the bot (logs: $LOG_DIR/bot.log)"
exec "$PROJECT_DIR/.venv/bin/signal-summarizer" run 2>&1 | tee -a "$LOG_DIR/bot.log"
