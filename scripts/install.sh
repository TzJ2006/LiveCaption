#!/usr/bin/env bash
set -euo pipefail

# Meeting Helper — macOS/Linux Installation Script
# Installs Screenpipe, configures MCP for Claude Code, and sets up the meeting summary pipe.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PIPE_NAME="meeting-summary-zh"
PIPE_SOURCE="$PROJECT_DIR/pipes/$PIPE_NAME"
PIPE_DEST="$HOME/.screenpipe/pipes/$PIPE_NAME"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ── Step 1: Check prerequisites ──────────────────────────────────────────────
info "Checking prerequisites..."

if ! command -v node &>/dev/null; then
    error "Node.js is required (>= 18). Install from https://nodejs.org/"
    exit 1
fi

NODE_VERSION=$(node -v | sed 's/v//' | cut -d. -f1)
if [ "$NODE_VERSION" -lt 18 ]; then
    error "Node.js >= 18 required. Current: $(node -v)"
    exit 1
fi
info "Node.js $(node -v) — OK"

if ! command -v npx &>/dev/null; then
    error "npx not found. Install Node.js >= 18."
    exit 1
fi

# ── Step 2: Check if Screenpipe is installed ─────────────────────────────────
info "Checking Screenpipe installation..."

if command -v screenpipe &>/dev/null; then
    info "Screenpipe CLI found: $(which screenpipe)"
elif [ -d "/Applications/Screenpipe.app" ] || [ -d "$HOME/Applications/Screenpipe.app" ]; then
    info "Screenpipe desktop app found."
else
    warn "Screenpipe not detected."
    echo ""
    echo "Install Screenpipe from: https://screenpi.pe/download"
    echo "  macOS:  brew install screenpipe (or download .dmg)"
    echo "  Linux:  npx screenpipe@latest record"
    echo ""
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# ── Step 3: Check macOS permissions ──────────────────────────────────────────
if [[ "$(uname)" == "Darwin" ]]; then
    info "macOS detected — checking permissions..."
    echo ""
    echo "  Screenpipe requires these permissions (grant manually if not done):"
    echo "    1. Screen Recording:  System Settings > Privacy & Security > Screen Recording"
    echo "    2. Accessibility:     System Settings > Privacy & Security > Accessibility"
    echo "    3. Microphone:        System Settings > Privacy & Security > Microphone"
    echo ""
    warn "If meeting detection doesn't work, verify Accessibility permission."
fi

# ── Step 4: Install the meeting summary pipe ─────────────────────────────────
info "Installing meeting summary pipe..."

mkdir -p "$PIPE_DEST"
if [ -f "$PIPE_SOURCE/pipe.md" ]; then
    cp "$PIPE_SOURCE/pipe.md" "$PIPE_DEST/pipe.md"
    info "Pipe installed to: $PIPE_DEST/pipe.md"
else
    error "Pipe source not found at: $PIPE_SOURCE/pipe.md"
    exit 1
fi

# ── Step 5: Setup MCP for Claude Code ────────────────────────────────────────
info "Setting up MCP for Claude Code..."

if command -v claude &>/dev/null; then
    claude mcp add screenpipe -- npx -y screenpipe-mcp 2>/dev/null && \
        info "MCP server 'screenpipe' added to Claude Code." || \
        warn "Could not auto-add MCP. Run manually: claude mcp add screenpipe -- npx -y screenpipe-mcp"
else
    warn "Claude Code CLI not found. Install from: https://claude.ai/code"
    echo "  After installing, run: claude mcp add screenpipe -- npx -y screenpipe-mcp"
fi

# ── Step 6: Summary ─────────────────────────────────────────────────────────
echo ""
info "Installation complete!"
echo ""
echo "  Quick Start:"
echo "    1. Start Screenpipe (app or CLI: screenpipe record)"
echo "    2. Join a meeting (Zoom, Teams, Meet)"
echo "    3. Open Claude Code: claude"
echo "    4. Ask: \"What's being discussed in my current meeting?\""
echo "    5. After meeting: \"Generate a summary of my last meeting\""
echo ""
echo "  Health check: bash $SCRIPT_DIR/health-check.sh"
echo ""
