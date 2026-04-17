#!/usr/bin/env bash
set -uo pipefail

# Meeting Helper — Health Check

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASS=0
FAIL=0
WARN=0

check_pass() { echo -e "  ${GREEN}[PASS]${NC} $1"; PASS=$((PASS+1)); }
check_fail() { echo -e "  ${RED}[FAIL]${NC} $1"; FAIL=$((FAIL+1)); }
check_warn() { echo -e "  ${YELLOW}[WARN]${NC} $1"; WARN=$((WARN+1)); }

echo "Meeting Helper — Health Check"
echo "=============================="
echo ""

# ── 1. Python3 ──────────────────────────────────────────────────────────────
echo "1. Python3"
if command -v python3 &>/dev/null; then
    check_pass "Python3 $(python3 --version 2>&1 | awk '{print $2}')"
else
    check_fail "Python3 not installed"
fi

# ── 2. 共用依赖 ─────────────────────────────────────────────────────────────
echo ""
echo "2. Common Dependencies"
for pkg in sounddevice numpy AppKit; do
    if python3 -c "import $pkg" &>/dev/null 2>&1; then
        check_pass "$pkg"
    else
        check_fail "$pkg not installed"
    fi
done

# ── 3. 模型后端 ─────────────────────────────────────────────────────────────
echo ""
echo "3. ASR Backends"

# Sherpa-ONNX
if python3 -c "import sherpa_onnx" &>/dev/null 2>&1; then
    check_pass "sherpa-onnx (for zipformer/paraformer)"
else
    check_warn "sherpa-onnx not installed (zipformer/paraformer unavailable)"
fi

# Qwen3-ASR
if python3 -c "import torch, qwen_asr" &>/dev/null 2>&1; then
    check_pass "torch + qwen-asr (for qwen3-asr)"
else
    check_warn "torch/qwen-asr not installed (qwen3-asr unavailable)"
fi

# Faster-Whisper
if python3 -c "import faster_whisper" &>/dev/null 2>&1; then
    check_pass "faster-whisper (for whisper)"
else
    check_warn "faster-whisper not installed (whisper unavailable)"
fi

# Moonshine
if python3 -c "import moonshine_voice" &>/dev/null 2>&1; then
    check_pass "moonshine-voice (for moonshine)"
else
    check_warn "moonshine-voice not installed (moonshine unavailable)"
fi

# Voxtral (voxmlx + mlx)
if python3 -c "import voxmlx, mlx" &>/dev/null 2>&1; then
    check_pass "voxmlx + mlx (for voxtral)"
else
    check_warn "voxmlx/mlx not installed (voxtral unavailable)"
fi

# ── 4. 本地模型文件 ─────────────────────────────────────────────────────────
echo ""
echo "4. Local Models"

MODELS_DIR="$HOME/.meeting-helper/models"

ZIPFORMER_DIR="$MODELS_DIR/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20"
if [ -f "$ZIPFORMER_DIR/tokens.txt" ] && [ -f "$ZIPFORMER_DIR/encoder-epoch-99-avg-1.onnx" ]; then
    check_pass "zipformer model installed"
else
    check_warn "zipformer model not found"
fi

PARAFORMER_DIR="$MODELS_DIR/sherpa-onnx-streaming-paraformer-bilingual-zh-en"
if [ -f "$PARAFORMER_DIR/tokens.txt" ] && [ -f "$PARAFORMER_DIR/encoder.int8.onnx" ]; then
    check_pass "paraformer model installed"
else
    check_warn "paraformer model not found"
fi

QWEN_CACHE="$HOME/.cache/huggingface/hub/models--Qwen--Qwen3-ASR-0.6B"
if [ -d "$QWEN_CACHE" ]; then
    CACHE_SIZE=$(du -sh "$QWEN_CACHE" 2>/dev/null | awk '{print $1}')
    check_pass "qwen3-asr cached ($CACHE_SIZE)"
else
    check_warn "qwen3-asr not cached (will download on first use)"
fi

WHISPER_CACHE="$HOME/.cache/huggingface/hub/models--Systran--faster-whisper-large-v3-turbo"
if [ -d "$WHISPER_CACHE" ]; then
    CACHE_SIZE=$(du -sh "$WHISPER_CACHE" 2>/dev/null | awk '{print $1}')
    check_pass "whisper large-v3-turbo cached ($CACHE_SIZE)"
else
    check_warn "whisper model not cached (will download on first use, ~1.6GB)"
fi

MOONSHINE_CACHE="$HOME/.cache/moonshine-voice"
if [ -d "$MOONSHINE_CACHE" ]; then
    CACHE_SIZE=$(du -sh "$MOONSHINE_CACHE" 2>/dev/null | awk '{print $1}')
    check_pass "moonshine models cached ($CACHE_SIZE)"
else
    check_warn "moonshine models not cached (will download on first use)"
fi

VOXTRAL_CACHE="$HOME/.cache/huggingface/hub/models--mlx-community--Voxtral-Mini-4B-Realtime-2602-4bit"
if [ -d "$VOXTRAL_CACHE" ]; then
    CACHE_SIZE=$(du -sh "$VOXTRAL_CACHE" 2>/dev/null | awk '{print $1}')
    check_pass "voxtral 4bit cached ($CACHE_SIZE)"
else
    check_warn "voxtral model not cached (will download on first use, ~3.1GB)"
fi

# ── 5. 字幕窗口进程 ─────────────────────────────────────────────────────────
echo ""
echo "5. Subtitle Window"
PID_FILE="$HOME/.meeting-helper/subtitle.pid"
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    check_pass "Subtitle window running (PID: $(cat "$PID_FILE"))"
else
    check_warn "Subtitle window not running. Start: bash scripts/start.sh"
fi

# ── 6. 字幕文件 ─────────────────────────────────────────────────────────────
echo ""
echo "6. Transcript Files"
TODAY=$(date +%Y-%m-%d)
TRANSCRIPT="$HOME/.meeting-helper/transcripts/$TODAY.txt"
if [ -f "$TRANSCRIPT" ]; then
    LINES=$(wc -l < "$TRANSCRIPT" | tr -d ' ')
    check_pass "Today's transcript: $TRANSCRIPT ($LINES lines)"
else
    check_warn "No transcript for today yet"
fi

# ── 7. Claude Code CLI ──────────────────────────────────────────────────────
echo ""
echo "7. Claude Code CLI"
if command -v claude &>/dev/null; then
    check_pass "Claude Code CLI found"
else
    check_fail "Claude Code CLI not found. Install: https://claude.ai/code"
fi

# ── Summary ─────────────────────────────────────────────────────────────────
echo ""
echo "=============================="
echo -e "Results: ${GREEN}$PASS passed${NC}, ${RED}$FAIL failed${NC}, ${YELLOW}$WARN warnings${NC}"
echo ""

if [ $FAIL -gt 0 ]; then
    echo "Fix the FAIL items above before using Meeting Helper."
    exit 1
elif [ $WARN -gt 0 ]; then
    echo "Some warnings — Meeting Helper may work with limited functionality."
    exit 0
else
    echo "All checks passed! You're ready to go."
    exit 0
fi
