#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BIN="$PROJECT_DIR/.build/live-subtitle"
HF_SCRIPT="$PROJECT_DIR/src/python/hf_asr_worker.py"
SHERPA_SCRIPT="$PROJECT_DIR/src/python/sherpa_asr_worker.py"
LOG_DIR="$PROJECT_DIR/logs"
PID_FILE="$LOG_DIR/subtitle.pid"
STOP_LOG="$LOG_DIR/subtitle-stop.log"
stopped=0

mkdir -p "$LOG_DIR"

log() {
    local message="$1"
    echo "$message"
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$message" >> "$STOP_LOG"
}

log "MeetingHelper stop started"

kill_pid() {
    local pid="$1"
    [[ -z "$pid" || "$pid" == "$$" ]] && return 1
    kill "$pid" 2>/dev/null || return 1
    for _ in $(seq 1 30); do
        kill -0 "$pid" 2>/dev/null || return 0
        sleep 0.1
    done
    kill -9 "$pid" 2>/dev/null || true
}

kill_tree() {
    local pid="$1"
    [[ -z "$pid" || "$pid" == "$$" ]] && return 1
    while IFS= read -r child; do
        [[ -z "$child" || "$child" == "$$" ]] && continue
        kill_tree "$child"
    done < <(pgrep -P "$pid" 2>/dev/null || true)
    kill_pid "$pid"
}

matches_command() {
    local pid="$1"
    local pattern="$2"
    local command
    command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    [[ "$command" == *"$pattern"* ]]
}

stop_pid() {
    local pid="$1"
    local label="$2"
    if kill_tree "$pid"; then
        log "$label (PID: $pid)"
        stopped=$((stopped + 1))
    fi
}

if [[ -f "$PID_FILE" ]]; then
    PID="$(cat "$PID_FILE")"
    if kill -0 "$PID" 2>/dev/null && matches_command "$PID" "$BIN"; then
        stop_pid "$PID" "Subtitle window stopped"
    fi
fi

for pattern in "$BIN" "$HF_SCRIPT" "$SHERPA_SCRIPT"; do
    PIDS="$(pgrep -f "$pattern" 2>/dev/null || true)"
    while IFS= read -r pid; do
        [[ -z "$pid" ]] && continue
        stop_pid "$pid" "Stopped leftover subtitle process"
    done <<< "$PIDS"
done

rm -f "$PID_FILE"

if [[ "$stopped" -eq 0 ]]; then
    log "No subtitle window running"
    exit 1
else
    log "MeetingHelper stop finished: stopped $stopped process(es)"
fi
