# Meeting Helper — Windows Installation Script
# Installs Screenpipe, configures MCP for Claude Code, and sets up the meeting summary pipe.

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$PipeName = "meeting-summary-zh"
$PipeSource = Join-Path $ProjectDir "pipes\$PipeName"
$PipeDest = Join-Path $env:USERPROFILE ".screenpipe\pipes\$PipeName"

function Write-Info($msg)  { Write-Host "[INFO] $msg" -ForegroundColor Green }
function Write-Warn($msg)  { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-Err($msg)   { Write-Host "[ERROR] $msg" -ForegroundColor Red }

# ── Step 1: Check prerequisites ──────────────────────────────────────────────
Write-Info "Checking prerequisites..."

$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) {
    Write-Err "Node.js is required (>= 18). Install from https://nodejs.org/"
    exit 1
}

$nodeVersion = (node -v) -replace 'v','' -split '\.' | Select-Object -First 1
if ([int]$nodeVersion -lt 18) {
    Write-Err "Node.js >= 18 required. Current: $(node -v)"
    exit 1
}
Write-Info "Node.js $(node -v) — OK"

# ── Step 2: Check Screenpipe ─────────────────────────────────────────────────
Write-Info "Checking Screenpipe installation..."

$sp = Get-Command screenpipe -ErrorAction SilentlyContinue
$spApp = Test-Path "$env:LOCALAPPDATA\Programs\Screenpipe\Screenpipe.exe"
if ($sp) {
    Write-Info "Screenpipe CLI found."
} elseif ($spApp) {
    Write-Info "Screenpipe desktop app found."
} else {
    Write-Warn "Screenpipe not detected."
    Write-Host ""
    Write-Host "  Install from: https://screenpi.pe/download"
    Write-Host "  Or: winget install screenpipe"
    Write-Host ""
    $reply = Read-Host "Continue anyway? (y/N)"
    if ($reply -ne 'y' -and $reply -ne 'Y') { exit 1 }
}

# ── Step 3: Windows permissions note ─────────────────────────────────────────
Write-Info "Windows permissions:"
Write-Host "  Screenpipe needs Microphone access: Settings > Privacy > Microphone"
Write-Host "  System audio capture uses WASAPI Loopback (no extra config needed)."
Write-Host ""

# ── Step 4: Install pipe ─────────────────────────────────────────────────────
Write-Info "Installing meeting summary pipe..."

if (-not (Test-Path $PipeDest)) {
    New-Item -ItemType Directory -Path $PipeDest -Force | Out-Null
}

$pipeFile = Join-Path $PipeSource "pipe.md"
if (Test-Path $pipeFile) {
    Copy-Item $pipeFile (Join-Path $PipeDest "pipe.md") -Force
    Write-Info "Pipe installed to: $PipeDest\pipe.md"
} else {
    Write-Err "Pipe source not found at: $pipeFile"
    exit 1
}

# ── Step 5: Setup MCP ────────────────────────────────────────────────────────
Write-Info "Setting up MCP for Claude Code..."

$claude = Get-Command claude -ErrorAction SilentlyContinue
if ($claude) {
    try {
        claude mcp add screenpipe -- npx -y screenpipe-mcp
        Write-Info "MCP server 'screenpipe' added to Claude Code."
    } catch {
        Write-Warn "Could not auto-add MCP. Run manually: claude mcp add screenpipe -- npx -y screenpipe-mcp"
    }
} else {
    Write-Warn "Claude Code CLI not found. Install from: https://claude.ai/code"
    Write-Host "  After installing, run: claude mcp add screenpipe -- npx -y screenpipe-mcp"
}

# ── Step 6: Summary ──────────────────────────────────────────────────────────
Write-Host ""
Write-Info "Installation complete!"
Write-Host ""
Write-Host "  Quick Start:"
Write-Host "    1. Start Screenpipe (app or CLI)"
Write-Host "    2. Join a meeting (Zoom, Teams, Meet)"
Write-Host "    3. Open Claude Code: claude"
Write-Host "    4. Ask: 'What's being discussed in my current meeting?'"
Write-Host "    5. After meeting: 'Generate a summary of my last meeting'"
Write-Host ""
