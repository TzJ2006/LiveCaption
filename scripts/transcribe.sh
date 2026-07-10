#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BIN="$PROJECT_DIR/.build/transcribe-audio"

mkdir -p "$PROJECT_DIR/.build/module-cache"
xcrun swiftc \
    -module-cache-path "$PROJECT_DIR/.build/module-cache" \
    "$SCRIPT_DIR/TranscribeAudio.swift" \
    -o "$BIN"

"$BIN" "$@"
