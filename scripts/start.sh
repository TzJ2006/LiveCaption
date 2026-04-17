#!/usr/bin/env bash
set -euo pipefail

# Meeting Helper — 一键启动
# 用法: bash scripts/start.sh [--model MODEL] [--source mic|system|both] [--system-device NAME]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$HOME/.meeting-helper"

# ── 参数解析 ────────────────────────────────────────────────────────────────
MODEL="${MODEL:-zipformer}"
SOURCE="${SOURCE:-mic}"
SYSTEM_DEVICE="${SYSTEM_DEVICE:-}"
SUBTITLE_OPACITY="${SUBTITLE_OPACITY:-0.75}"
SUBTITLE_HEIGHT="${SUBTITLE_HEIGHT:-120}"
OUTPUT_DIR="${OUTPUT_DIR:-$HOME/.meeting-helper/transcripts}"

while [[ $# -gt 0 ]]; do
    case $1 in
        --model) MODEL="$2"; shift 2 ;;
        --source) SOURCE="$2"; shift 2 ;;
        --system-device) SYSTEM_DEVICE="$2"; shift 2 ;;
        --opacity) SUBTITLE_OPACITY="$2"; shift 2 ;;
        --height) SUBTITLE_HEIGHT="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ── 0. 创建必要目录 ─────────────────────────────────────────────────────────
mkdir -p "$LOG_DIR" "$OUTPUT_DIR"

# ── 1. 检查参数有效性 ──────────────────────────────────────────────────────
case "$MODEL" in
    zipformer|paraformer|qwen3-asr|whisper|moonshine|voxtral)
        info "模型: $MODEL"
        ;;
    *)
        error "未知模型: $MODEL"
        echo "  可用: zipformer (默认), paraformer, qwen3-asr, whisper, moonshine, voxtral"
        exit 1
        ;;
esac

case "$SOURCE" in
    mic|system|both)
        info "音频源: $SOURCE"
        ;;
    *)
        error "未知音频源: $SOURCE"
        echo "  可用: mic (默认), system, both"
        exit 1
        ;;
esac

# ── 2. 检查是否已在运行 ────────────────────────────────────────────────────
PID_FILE="$LOG_DIR/subtitle.pid"
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        info "字幕窗口已在运行 (PID: $OLD_PID)"
        exit 0
    fi
    rm -f "$PID_FILE"
fi

# ── 3. 启动字幕窗口 ─────────────────────────────────────────────────────────
info "启动字幕窗口..."
SUBTITLE_ARGS=(
    --model "$MODEL"
    --source "$SOURCE"
    --output-dir "$OUTPUT_DIR"
    --opacity "$SUBTITLE_OPACITY"
    --height "$SUBTITLE_HEIGHT"
)
if [[ -n "$SYSTEM_DEVICE" ]]; then
    SUBTITLE_ARGS+=(--system-device "$SYSTEM_DEVICE")
fi
PYTHONUNBUFFERED=1 nohup python3 "$SCRIPT_DIR/live-subtitle.py" \
    "${SUBTITLE_ARGS[@]}" \
    > "$LOG_DIR/subtitle.log" 2>&1 &
SUBTITLE_PID=$!
echo "$SUBTITLE_PID" > "$PID_FILE"
info "字幕窗口已启动 (PID: $SUBTITLE_PID)"

# ── 4. 完成 ────────────────────────────────────────────────────────────────
echo ""
info "Meeting Helper 已就绪!"
echo ""
echo "  模型: $MODEL"
echo "  音频源: $SOURCE"
if [[ -n "$SYSTEM_DEVICE" ]]; then
    echo "  系统音频设备: $SYSTEM_DEVICE"
fi
echo ""
echo "  字幕控制:"
echo "    Cmd+Shift+S  显示/隐藏字幕窗口"
echo ""
echo "  字幕文件:"
echo "    $OUTPUT_DIR/"
echo ""
echo "  示例:"
echo "    bash $SCRIPT_DIR/start.sh --source both"
echo "    bash $SCRIPT_DIR/start.sh --model paraformer --source system"
echo ""
echo "  停止服务:"
echo "    bash $SCRIPT_DIR/stop.sh"
echo ""
