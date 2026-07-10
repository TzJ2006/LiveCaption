#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="$PROJECT_DIR/.build"
BIN="$BUILD_DIR/live-subtitle"
LOG_DIR="$PROJECT_DIR/logs"
PID_FILE="$LOG_DIR/subtitle.pid"

SOURCE="${SOURCE:-mic}"
ASR="${ASR:-apple}"
LANGUAGE="${LANGUAGE:-zh-CN}"
HF_MODEL="${HF_MODEL:-}"
OUTPUT_DIR="${OUTPUT_DIR:-$PROJECT_DIR/transcripts}"
OPACITY="${SUBTITLE_OPACITY:-0.75}"
HEIGHT="${SUBTITLE_HEIGHT:-120}"
DEBUG="${DEBUG:-0}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --source) SOURCE="$2"; shift 2 ;;
        --asr) ASR="$2"; shift 2 ;;
        --language) LANGUAGE="$2"; shift 2 ;;
        --hf-model) HF_MODEL="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        --opacity) OPACITY="$2"; shift 2 ;;
        --height) HEIGHT="$2"; shift 2 ;;
        --debug) DEBUG="1"; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

case "$SOURCE" in
    mic|system|both) ;;
    *) echo "Use --source mic|system|both"; exit 1 ;;
esac

case "$ASR" in
    apple|hf|sherpa) ;;
    *) echo "Use --asr apple|hf|sherpa"; exit 1 ;;
esac

if [[ "$ASR" == "hf" && -z "$HF_MODEL" ]]; then
    echo "--asr hf requires --hf-model <huggingface/model-id>"
    exit 1
fi

if [[ "$ASR" == "sherpa" ]]; then
    bash "$SCRIPT_DIR/setup-sherpa.sh"
fi

mkdir -p "$BUILD_DIR/module-cache" "$LOG_DIR" "$OUTPUT_DIR"

if [[ -f "$PID_FILE" ]]; then
    OLD_PID="$(cat "$PID_FILE")"
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Subtitle window already running (PID: $OLD_PID)"
        exit 0
    fi
    rm -f "$PID_FILE"
fi

xcrun swiftc \
    -module-cache-path "$BUILD_DIR/module-cache" \
    "$SCRIPT_DIR/LiveSubtitle.swift" \
    -o "$BIN"

ARGS=(
    --source "$SOURCE"
    --asr "$ASR"
    --language "$LANGUAGE"
    --output-dir "$OUTPUT_DIR"
    --opacity "$OPACITY"
    --height "$HEIGHT"
    --hf-script "$SCRIPT_DIR/hf_asr.py"
    --sherpa-script "$SCRIPT_DIR/sherpa_asr.py"
)
if [[ "$ASR" == "hf" ]]; then
    ARGS+=(--hf-model "$HF_MODEL")
fi
if [[ "$DEBUG" == "1" ]]; then
    ARGS+=(--debug)
fi

nohup "$BIN" "${ARGS[@]}" > "$LOG_DIR/subtitle.log" 2>&1 &
PID=$!
echo "$PID" > "$PID_FILE"

echo "Subtitle window started (PID: $PID)"
echo "Source: $SOURCE"
echo "ASR: $ASR"
echo "Language: $LANGUAGE"
echo "Transcripts: $OUTPUT_DIR"
if [[ "$DEBUG" == "1" ]]; then
    echo "Debug audio: $PROJECT_DIR/debug-audio"
fi
echo "Log: $LOG_DIR/subtitle.log"
echo "Stop: bash $SCRIPT_DIR/stop.sh"
