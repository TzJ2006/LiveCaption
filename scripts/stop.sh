#!/usr/bin/env bash
set -uo pipefail

# Meeting Helper — 停止字幕窗口

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PID_FILE="$HOME/.meeting-helper/subtitle.pid"

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        info "字幕窗口已停止 (PID: $PID)"
    else
        warn "字幕窗口 PID $PID 已不存在"
    fi
    rm -f "$PID_FILE"
else
    # 尝试找到进程
    PIDS=$(pgrep -f "live-subtitle.py" 2>/dev/null || true)
    if [ -n "$PIDS" ]; then
        echo "$PIDS" | xargs kill 2>/dev/null
        info "已停止字幕窗口进程"
    else
        warn "未找到运行中的字幕窗口"
    fi
fi
