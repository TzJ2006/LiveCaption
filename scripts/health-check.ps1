# Meeting Helper — Health Check (Windows)
# Verifies all components are running and configured correctly.

$Pass = 0
$Fail = 0
$Warn = 0

function Check-Pass($msg) { Write-Host "  [PASS] $msg" -ForegroundColor Green; $script:Pass++ }
function Check-Fail($msg) { Write-Host "  [FAIL] $msg" -ForegroundColor Red; $script:Fail++ }
function Check-Warn($msg) { Write-Host "  [WARN] $msg" -ForegroundColor Yellow; $script:Warn++ }

Write-Host "Meeting Helper — Health Check"
Write-Host "=============================="
Write-Host ""

# -- 1. Screenpipe API --
Write-Host "1. Screenpipe API (localhost:3030)"
try {
    $health = Invoke-RestMethod -Uri "http://localhost:3030/health" -TimeoutSec 3 -ErrorAction Stop
    Check-Pass "Screenpipe is running"
} catch {
    Check-Fail "Screenpipe not reachable at localhost:3030"
    Write-Host "         Start it: screenpipe record (or open the desktop app)"
}

# -- 2. Meeting detection --
Write-Host ""
Write-Host "2. Meeting Detection"
try {
    $meetings = Invoke-RestMethod -Uri "http://localhost:3030/meetings?limit=1" -TimeoutSec 3 -ErrorAction Stop
    Check-Pass "Meeting API endpoint accessible"
} catch {
    Check-Warn "Could not query meetings endpoint"
}

# -- 3. Audio transcription --
Write-Host ""
Write-Host "3. Audio Transcription"
try {
    $audio = Invoke-WebRequest -Uri "http://localhost:3030/search?content_type=audio&limit=1" -TimeoutSec 3 -ErrorAction Stop
    if ($audio.Content -match "transcription") {
        Check-Pass "Audio transcriptions are being generated"
    } else {
        Check-Warn "No audio transcriptions found (is the microphone configured?)"
    }
} catch {
    Check-Warn "No audio transcriptions found (is the microphone configured?)"
}

# -- 4. Screen capture --
Write-Host ""
Write-Host "4. Screen Capture"
try {
    $frames = Invoke-WebRequest -Uri "http://localhost:3030/search?content_type=ocr&limit=1" -TimeoutSec 3 -ErrorAction Stop
    if ($frames.Content -match "content") {
        Check-Pass "Screen capture with OCR is working"
    } else {
        Check-Warn "No screen captures found (check screen recording permissions)"
    }
} catch {
    Check-Warn "No screen captures found (check screen recording permissions)"
}

# -- 5. Node.js --
Write-Host ""
Write-Host "5. Node.js"
$node = Get-Command node -ErrorAction SilentlyContinue
if ($node) {
    $nodeVer = (node -v) -replace 'v','' -split '\.' | Select-Object -First 1
    if ([int]$nodeVer -ge 18) {
        Check-Pass "Node.js $(node -v)"
    } else {
        Check-Fail "Node.js >= 18 required. Current: $(node -v)"
    }
} else {
    Check-Fail "Node.js not installed"
}

# -- 6. Claude Code CLI --
Write-Host ""
Write-Host "6. Claude Code CLI"
$claude = Get-Command claude -ErrorAction SilentlyContinue
if ($claude) {
    Check-Pass "Claude Code CLI found"
} else {
    Check-Fail "Claude Code CLI not found. Install: https://claude.ai/code"
}

# -- 7. MCP configuration --
Write-Host ""
Write-Host "7. MCP Configuration"
if ($claude) {
    try {
        $mcpList = claude mcp list 2>$null
        if ($mcpList -match "screenpipe") {
            Check-Pass "screenpipe MCP server configured in Claude Code"
        } else {
            Check-Fail "screenpipe MCP not found. Run: claude mcp add screenpipe -- npx -y screenpipe-mcp"
        }
    } catch {
        Check-Warn "Could not check MCP configuration"
    }
} else {
    Check-Warn "Skipped (Claude Code not installed)"
}

# -- 8. Meeting summary pipe --
Write-Host ""
Write-Host "8. Meeting Summary Pipe"
$pipePath = Join-Path $env:USERPROFILE ".screenpipe\pipes\meeting-summary-zh\pipe.md"
if (Test-Path $pipePath) {
    Check-Pass "Custom pipe installed at $pipePath"
} else {
    Check-Warn "Pipe not installed. Run: powershell -File scripts\install.ps1"
}

# -- Summary --
Write-Host ""
Write-Host "=============================="
Write-Host -NoNewline "Results: "
Write-Host -NoNewline "$Pass passed" -ForegroundColor Green
Write-Host -NoNewline ", $Fail failed" -ForegroundColor Red
Write-Host ", $Warn warnings" -ForegroundColor Yellow
Write-Host ""

if ($Fail -gt 0) {
    Write-Host "Fix the FAIL items above before using Meeting Helper."
    exit 1
} elseif ($Warn -gt 0) {
    Write-Host "Some warnings — Meeting Helper may work with limited functionality."
    exit 0
} else {
    Write-Host "All checks passed! You're ready to go."
    exit 0
}
