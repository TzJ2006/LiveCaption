#!/usr/bin/env bash
set -uo pipefail

# Meeting Helper — Health Check
# Verifies all components are running and configured correctly.

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

# ── 1. Screenpipe API ────────────────────────────────────────────────────────
echo "1. Screenpipe API (localhost:3030)"
if curl -s --connect-timeout 3 http://localhost:3030/health >/dev/null 2>&1; then
    check_pass "Screenpipe is running"
else
    check_fail "Screenpipe not reachable at localhost:3030"
    echo "         Start it: screenpipe (or open the desktop app)"
fi

# ── 2. Meeting detection ─────────────────────────────────────────────────────
echo ""
echo "2. Meeting Detection"
if MEETINGS=$(curl -s --connect-timeout 3 "http://localhost:3030/meetings?limit=1" 2>/dev/null) && [ -n "$MEETINGS" ]; then
    check_pass "Meeting API endpoint accessible"
else
    check_warn "Could not query meetings endpoint"
fi

# ── 3. Audio transcription ───────────────────────────────────────────────────
echo ""
echo "3. Audio Transcription"
if AUDIO=$(curl -s --connect-timeout 3 "http://localhost:3030/search?content_type=audio&limit=1" 2>/dev/null) && echo "$AUDIO" | grep -q "transcription"; then
    check_pass "Audio transcriptions are being generated"
else
    check_warn "No audio transcriptions found (is the microphone configured?)"
fi

# ── 4. Screen capture ────────────────────────────────────────────────────────
echo ""
echo "4. Screen Capture"
if FRAMES=$(curl -s --connect-timeout 3 "http://localhost:3030/search?content_type=ocr&limit=1" 2>/dev/null) && echo "$FRAMES" | grep -q "content"; then
    check_pass "Screen capture with OCR is working"
else
    check_warn "No screen captures found (check Screen Recording permission on macOS)"
fi

# ── 5. Node.js ───────────────────────────────────────────────────────────────
echo ""
echo "5. Node.js"
if command -v node &>/dev/null; then
    NODE_VER=$(node -v | sed 's/v//' | cut -d. -f1)
    if [ "$NODE_VER" -ge 18 ]; then
        check_pass "Node.js $(node -v)"
    else
        check_fail "Node.js >= 18 required. Current: $(node -v)"
    fi
else
    check_fail "Node.js not installed"
fi

# ── 6. Claude Code CLI ───────────────────────────────────────────────────────
echo ""
echo "6. Claude Code CLI"
if command -v claude &>/dev/null; then
    check_pass "Claude Code CLI found"
else
    check_fail "Claude Code CLI not found. Install: https://claude.ai/code"
fi

# ── 7. MCP configuration ────────────────────────────────────────────────────
echo ""
echo "7. MCP Configuration"
if command -v claude &>/dev/null; then
    MCP_LIST=$(claude mcp list 2>/dev/null || true)
    if echo "$MCP_LIST" | grep -qi "screenpipe"; then
        check_pass "screenpipe MCP server configured in Claude Code"
    else
        check_fail "screenpipe MCP not found. Run: claude mcp add screenpipe -- npx -y screenpipe-mcp"
    fi
else
    check_warn "Skipped (Claude Code not installed)"
fi

# ── 8. Meeting summary pipe ─────────────────────────────────────────────────
echo ""
echo "8. Meeting Summary Pipe"
PIPE_PATH="$HOME/.screenpipe/pipes/meeting-summary-zh/pipe.md"
if [ -f "$PIPE_PATH" ]; then
    check_pass "Custom pipe installed at $PIPE_PATH"
else
    check_warn "Pipe not installed. Run: bash scripts/install.sh"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
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
