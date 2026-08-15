#!/bin/bash
#
# Install signal-cli inside the proot Debian userland, with a libsignal native
# library that works on arm64.
#
# Upstream ships native libsignal only for x86_64 Linux, Windows and macOS
# ("For other systems/architectures see: Provide native lib for libsignal"), so
# on a phone the bundled .so is useless. exquo/signal-libs-build publishes
# prebuilt aarch64 libraries; this script works out which libsignal version the
# signal-cli release wants, downloads the matching one, and swaps it into the
# jar using whatever naming convention that jar already uses.
#
# Run inside Debian:  SIGNAL_CLI_VERSION=0.14.7 bash install-signal-cli.sh
set -euo pipefail

VERSION="${SIGNAL_CLI_VERSION:-0.14.7}"
PREFIX="${PREFIX:-/opt}"
ARCH="$(uname -m)"

die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

case "$ARCH" in
    aarch64|arm64) TRIPLE="aarch64-unknown-linux-gnu"; JAR_ARCH="aarch64" ;;
    armv7l|armv8l) TRIPLE="armv7-unknown-linux-gnueabihf"; JAR_ARCH="arm" ;;
    x86_64)        echo "x86_64: the bundled native lib is fine, nothing to patch"
                   TRIPLE=""; JAR_ARCH="" ;;
    *) die "unsupported architecture $ARCH" ;;
esac

cd /tmp
echo ">> downloading signal-cli $VERSION"
wget -q --show-progress -O "signal-cli-$VERSION.tar.gz" \
    "https://github.com/AsamK/signal-cli/releases/download/v$VERSION/signal-cli-$VERSION.tar.gz"
rm -rf "$PREFIX/signal-cli-$VERSION"
tar xf "signal-cli-$VERSION.tar.gz" -C "$PREFIX"
ln -sf "$PREFIX/signal-cli-$VERSION/bin/signal-cli" /usr/local/bin/signal-cli

if [ -n "$TRIPLE" ]; then
    JAR="$(ls "$PREFIX/signal-cli-$VERSION"/lib/libsignal-client-*.jar 2>/dev/null | head -1)"
    [ -n "$JAR" ] || die "no libsignal-client jar in the signal-cli release"
    LIBSIGNAL_VERSION="$(basename "$JAR" | sed -E 's/libsignal-client-(.*)\.jar/\1/')"
    echo ">> signal-cli $VERSION wants libsignal $LIBSIGNAL_VERSION"

    # Mirror the naming the jar already uses for the bundled library, rather
    # than guessing at it: libsignal_jni_amd64.so -> libsignal_jni_aarch64.so
    BUNDLED="$(unzip -Z1 "$JAR" | grep -E '^libsignal_jni.*\.so$' | head -1)" \
        || die "no libsignal_jni entry in $JAR — the packaging changed"
    [ -n "$BUNDLED" ] || die "no libsignal_jni entry in $JAR — the packaging changed"
    TARGET="$(echo "$BUNDLED" | sed -E "s/(libsignal_jni)(_[a-z0-9]+)?\.so/\1_${JAR_ARCH}.so/")"
    echo ">> jar bundles $BUNDLED; installing $TARGET"

    ASSET="libsignal_jni.so-v$LIBSIGNAL_VERSION-$TRIPLE.tar.gz"
    URL="https://github.com/exquo/signal-libs-build/releases/download/libsignal_v$LIBSIGNAL_VERSION/$ASSET"
    echo ">> downloading $ASSET"
    rm -rf /tmp/libsignal && mkdir -p /tmp/libsignal
    wget -q --show-progress -O "/tmp/libsignal/$ASSET" "$URL" || die \
        "no prebuilt libsignal $LIBSIGNAL_VERSION for $TRIPLE at $URL
 Either pin an older SIGNAL_CLI_VERSION whose libsignal has a build, or build
 libsignal yourself: https://github.com/AsamK/signal-cli/wiki/Provide-native-lib-for-libsignal"
    tar xf "/tmp/libsignal/$ASSET" -C /tmp/libsignal
    SO="$(find /tmp/libsignal -name 'libsignal_jni*.so' | head -1)"
    [ -n "$SO" ] || die "the downloaded archive contained no .so"

    cp "$SO" "/tmp/libsignal/$TARGET"
    (cd /tmp/libsignal && zip -q "$JAR" "$TARGET")
    echo ">> patched $(basename "$JAR")"
fi

echo ">> verifying"
signal-cli --version || die "signal-cli will not start.
 If it complains about the Java version, install a newer JRE (signal-cli 0.14.x
 wants JRE 25) or set SIGNAL_CLI_VERSION to an older release and re-run.
 If it complains about libsignal, the native library did not take effect."
