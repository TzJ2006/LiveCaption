#!/usr/bin/env bash
set -euo pipefail

# Start Screenpipe using npx (latest version)
# This bypasses the deprecated Homebrew version

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

warn "Using npx to run latest Screenpipe version (bypassing Homebrew's deprecated v0.2.13)"
info "First run may take a few minutes to download..."
echo ""

# Run latest Screenpipe via npx
npx screenpipe@latest
