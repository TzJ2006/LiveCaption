#!/usr/bin/env bash
set -euo pipefail

# Setup screenpipe-mcp for Claude Code CLI

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

if ! command -v claude &>/dev/null; then
    error "Claude Code CLI not found."
    echo "  Install from: https://claude.ai/code"
    echo "  Or: npm install -g @anthropic-ai/claude-code"
    exit 1
fi

info "Adding screenpipe MCP server to Claude Code..."

# Add the screenpipe MCP server
claude mcp add screenpipe -- npx -y screenpipe-mcp

info "MCP server added successfully."
echo ""

# Verify
info "Verifying MCP configuration..."
claude mcp list 2>/dev/null | head -20

echo ""
info "Done! Start Claude Code with: claude"
echo "  Then ask: \"List my recent meetings\" to verify the connection."
