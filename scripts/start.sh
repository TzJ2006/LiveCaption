#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# ponytail: non-macOS -> Python host (Windows WASAPI loopback), same flags
if [[ "$(uname -s)" != "Darwin" ]]; then
    if grep -qi microsoft /proc/version 2>/dev/null; then
        echo "WSL cannot capture Windows audio. Run scripts\\start.bat from PowerShell instead;"
        echo "WSL is only used as the ASR backend via --asr wsl-vllm."
        exit 1
    fi
    exec python3 "$PROJECT_DIR/src/python/win_host.py" "$@"
fi
BUILD_DIR="$PROJECT_DIR/.build"
BIN="$BUILD_DIR/live-subtitle"
LOG_DIR="$PROJECT_DIR/logs"
PID_FILE="$LOG_DIR/subtitle.pid"

CONFIG="${CONFIG:-$PROJECT_DIR/config.json}"
for ((i = 1; i <= $#; i++)); do  # --config has to be known before the file is read
    [[ "${!i}" == "--config" ]] && CONFIG="${@:i+1:1}"
done

# ponytail: the Swift host takes CLI flags only, so config.json is resolved here instead -- that
# keeps one config file for both hosts (win_host.py loads it as argparse defaults). Precedence is
# built-in default < config.json < environment variable < CLI flag, so nothing here can override
# something the caller actually typed.
if [[ -f "$CONFIG" ]]; then
    eval "$(python3 "$SCRIPT_DIR/config_to_env.py" "$CONFIG")"
fi

SOURCE="${SOURCE:-${CFG_SOURCE:-auto}}"
ASR="${ASR:-${CFG_ASR:-apple}}"
LANGUAGE="${LANGUAGE:-${CFG_LANGUAGE:-zh-CN}}"
HF_MODEL="${HF_MODEL:-${CFG_HF_MODEL:-}}"
HF_MODELS="${HF_MODELS:-${CFG_HF_MODELS:-}}"
# streaming checkpoints need transformers >= 5.13, which qwen-asr (pinned at 4.57) will not share;
# setup-hf-stream.sh builds that second environment under .build/stream-env
HF_STREAM_PYTHON="${HF_STREAM_PYTHON:-${CFG_HF_STREAM_PYTHON:-}}"
OUTPUT_DIR="${OUTPUT_DIR:-${CFG_OUTPUT_DIR:-$PROJECT_DIR/transcripts}}"
OPACITY="${SUBTITLE_OPACITY:-${CFG_OPACITY:-0.75}}"
HEIGHT="${SUBTITLE_HEIGHT:-${CFG_HEIGHT:-120}}"
DEBUG="${DEBUG:-${CFG_DEBUG:-0}}"
RECORD="${RECORD:-${CFG_RECORD:-0}}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config) shift 2 ;;
        --source) SOURCE="$2"; shift 2 ;;
        --asr) ASR="$2"; shift 2 ;;
        --language) LANGUAGE="$2"; shift 2 ;;
        --hf-model) HF_MODEL="$2"; shift 2 ;;
        --hf-models) HF_MODELS="$2"; shift 2 ;;
        --hf-stream-python) HF_STREAM_PYTHON="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        --opacity) OPACITY="$2"; shift 2 ;;
        --height) HEIGHT="$2"; shift 2 ;;
        --debug) DEBUG="1"; shift ;;
        --record) RECORD="1"; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

case "$SOURCE" in
    mic|system|auto) ;;
    # the caption window is single-pane now; keep older configs and habits working
    both) echo "note: --source both is retired (one caption pane now); using auto"; SOURCE=auto ;;
    *) echo "Use --source mic|system|auto"; exit 1 ;;
esac

case "$ASR" in
    apple|hf|hf-stream|sherpa) ;;
    *) echo "Use --asr apple|hf|hf-stream|sherpa"; exit 1 ;;
esac

if [[ "$ASR" == "hf" || "$ASR" == "hf-stream" ]] && [[ -z "$HF_MODEL" ]]; then
    echo "--asr $ASR requires --hf-model <huggingface/model-id>"
    exit 1
fi

if [[ "$ASR" == "sherpa" ]]; then
    bash "$SCRIPT_DIR/setup-sherpa.sh"
fi

# the dropdown can reach a streaming model without starting on one, so build its environment
# whenever any entry would land on that path
if [[ "$ASR" == "hf-stream" || "$HF_MODEL$HF_MODELS" == *streaming* || "$HF_MODELS" == *stream:* ]]; then
    bash "$SCRIPT_DIR/setup-hf-stream.sh"
fi
if [[ -z "$HF_STREAM_PYTHON" && -x "$BUILD_DIR/stream-env/bin/python" ]]; then
    HF_STREAM_PYTHON="$BUILD_DIR/stream-env/bin/python"
fi
HF_STREAM_PYTHON="${HF_STREAM_PYTHON:-python3}"

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
    "$PROJECT_DIR/src/swift/LiveSubtitle.swift" \
    -o "$BIN"

ARGS=(
    --source "$SOURCE"
    --asr "$ASR"
    --language "$LANGUAGE"
    --output-dir "$OUTPUT_DIR"
    --opacity "$OPACITY"
    --height "$HEIGHT"
    --hf-script "$PROJECT_DIR/src/python/hf_asr_worker.py"
    --hf-stream-script "$PROJECT_DIR/src/python/hf_stream_worker.py"
    --hf-stream-python "$HF_STREAM_PYTHON"
    --sherpa-script "$PROJECT_DIR/src/python/sherpa_asr_worker.py"
)
if [[ -n "$HF_MODEL" ]]; then
    ARGS+=(--hf-model "$HF_MODEL")
fi
# extra entries for the caption bar's model dropdown; the running model can be switched live
if [[ -n "$HF_MODELS" ]]; then
    ARGS+=(--hf-models "$HF_MODELS")
fi
if [[ "$DEBUG" == "1" ]]; then
    ARGS+=(--debug)
fi
if [[ "$RECORD" == "1" ]]; then
    ARGS+=(--record)
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
if [[ "$RECORD" == "1" ]]; then
    echo "Recordings: $PROJECT_DIR/recordings"
fi
echo "Log: $LOG_DIR/subtitle.log"
echo "Stop: bash $SCRIPT_DIR/stop.sh"
