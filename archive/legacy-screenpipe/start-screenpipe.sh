#!/usr/bin/env bash
set -euo pipefail

# Quick start script for Screenpipe

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }

# Check if Screenpipe is already running
if curl -s --connect-timeout 3 http://localhost:3030/health >/dev/null 2>&1; then
    info "Screenpipe is already running on port 3030"
    exit 0
fi

info "Starting Screenpipe..."
echo ""
echo "  Using default settings:"
echo "    - FPS: 0.2 (optimized for low storage)"
echo "    - Audio chunk: 30 seconds"
echo "    - Port: 3030"
echo "    - Transcription: whisper-large-v3-turbo"
echo "    - OCR: apple-native (macOS)"
echo ""

# Start Screenpipe
screenpipe

# Note: The command above will run in foreground
# To run in background, use: screenpipe > ~/.screenpipe/screenpipe.log 2>&1 &
