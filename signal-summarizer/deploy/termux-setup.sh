#!/data/data/com.termux/files/usr/bin/bash
#
# One-time setup for running signal-summarizer entirely on an Android phone.
#
#   pkg install git && git clone <this repo> ~/signal-summarizer
#   bash ~/signal-summarizer/signal-summarizer/deploy/termux-setup.sh
#
# What ends up where, and why:
#
#   Termux (bionic libc)      llama.cpp + Python bot   — compiled here, fast
#   proot Debian (glibc)      signal-cli + a JRE       — signal-cli's native
#                                                        libsignal needs glibc
#
# Both share the phone's network namespace, so they talk over 127.0.0.1.
#
# Every version below can be overridden from the environment, e.g.
#   SIGNAL_CLI_VERSION=0.14.6 bash termux-setup.sh
set -euo pipefail

SIGNAL_CLI_VERSION="${SIGNAL_CLI_VERSION:-0.14.7}"
MODEL_REPO="${MODEL_REPO:-bartowski/Llama-3.2-1B-Instruct-GGUF}"
MODEL_QUANT="${MODEL_QUANT:-Q4_K_M}"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LLAMA_DIR="${LLAMA_DIR:-$HOME/llama.cpp}"
DEBIAN="proot-distro login debian --"

say() { printf '\n=== %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[ -d /data/data/com.termux ] || die "this script is meant to run inside Termux"

say "1/6  Termux packages"
pkg update -y
pkg install -y python git cmake clang make openssl libcurl proot-distro termux-api

say "2/6  Building llama.cpp (this takes a few minutes)"
if [ ! -x "$LLAMA_DIR/build/bin/llama-cli" ]; then
    [ -d "$LLAMA_DIR" ] || git clone --depth 1 https://github.com/ggml-org/llama.cpp "$LLAMA_DIR"
    cmake -B "$LLAMA_DIR/build" -S "$LLAMA_DIR" -DCMAKE_BUILD_TYPE=Release
    cmake --build "$LLAMA_DIR/build" --config Release -j "$(nproc)" \
        --target llama-cli llama-server
fi
"$LLAMA_DIR/build/bin/llama-cli" --version || die "llama.cpp build failed"

say "3/6  Fetching the model ($MODEL_REPO:$MODEL_QUANT)"
# -hf resolves the GGUF file for us and caches it under ~/.cache/llama.cpp,
# so no download URL is hard-coded here. Ctrl-C once it reports the load is
# done; we only want the file on disk.
"$LLAMA_DIR/build/bin/llama-cli" -hf "$MODEL_REPO:$MODEL_QUANT" \
    -p "hello" -n 8 -no-cnv --no-display-prompt >/dev/null || \
    die "model download failed — check the repo name, or download a .gguf by hand"

say "4/6  Debian userland for signal-cli"
proot-distro install debian 2>/dev/null || echo "(debian already installed)"
$DEBIAN bash -lc "apt-get update -qq && apt-get install -y -qq \
    default-jre-headless wget ca-certificates unzip zip file"

say "5/6  signal-cli $SIGNAL_CLI_VERSION with an aarch64 libsignal"
$DEBIAN bash -lc "SIGNAL_CLI_VERSION=$SIGNAL_CLI_VERSION bash -s" \
    < "$PROJECT_DIR/deploy/install-signal-cli.sh"

say "6/6  The bot itself"
cd "$PROJECT_DIR"
python -m venv --system-site-packages .venv
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q -e .          # no compiled dependencies
cp -n deploy/phone.env "$HOME/signal-summarizer.env" 2>/dev/null || true

cat <<EOF

Setup finished.

Next:
  1. Link the bot to your Signal account (shows a URI to turn into a QR code
     and scan from Signal > Settings > Linked devices):

       proot-distro login debian -- signal-cli link -n summarizer-phone

  2. Put your number in ~/signal-summarizer.env (SIGNAL_ACCOUNT=+15551234567).

  3. Start everything:

       bash $PROJECT_DIR/deploy/termux-run.sh

  Battery: Android will kill background processes unless you exempt Termux.
  Settings > Apps > Termux > Battery > Unrestricted, and keep the Termux
  notification's "acquire wakelock" enabled (termux-run.sh takes one for you).
EOF
