# Meeting Helper

AI-powered meeting assistant built on [Screenpipe](https://github.com/screenpipe/screenpipe) + [Claude Code CLI](https://claude.ai/code).

Captures meeting audio and screen, generates transcripts, provides real-time Q&A, and produces comprehensive summaries — all running locally on your machine.

## How It Works

```
Screenpipe (background)          Claude Code CLI (your terminal)
┌──────────────────────┐         ┌──────────────────────────┐
│ Mic + System Audio   │         │ "What did we discuss?"   │
│ Screen Capture + OCR │◄──MCP──►│ "Generate meeting summary"│
│ Whisper Transcription│         │ "What did Alice say?"    │
│ Meeting Detection    │         │ "List action items"      │
└──────────────────────┘         └──────────────────────────┘
```

1. **Screenpipe** runs in the background capturing everything: mic audio, system audio (Zoom/Teams/Meet), screen content, and generating transcripts via Whisper
2. **Claude Code CLI** connects to Screenpipe via MCP (Model Context Protocol)
3. You ask questions in natural language — Claude searches your meeting data and responds

## Features

- **Audio capture**: Microphone + system audio (works with Zoom, Teams, Meet, Slack)
- **Real-time transcription**: Whisper large-v3-turbo with Chinese-English support
- **Meeting detection**: Auto-detects when you join/leave meetings
- **Speaker identification**: Voice-based diarization with calendar-assisted naming
- **Screen context**: Event-driven screenshots with OCR
- **Q&A during meetings**: Ask about what's being discussed right now
- **Comprehensive summaries**: Discussion points, decisions, action items, open questions
- **Bilingual**: Works with Chinese, English, and mixed-language meetings

## Quick Start

### Prerequisites

- [Node.js](https://nodejs.org/) >= 18
- [Screenpipe](https://screenpi.pe/download) (desktop app or CLI)
- [Claude Code CLI](https://claude.ai/code)
- Anthropic API key (for Claude)

### Install

**macOS / Linux:**
```bash
git clone https://github.com/TzJ2006/MeetingHelper.git
cd MeetingHelper
bash scripts/install.sh
```

**Windows (PowerShell):**
```powershell
git clone https://github.com/TzJ2006/MeetingHelper.git
cd MeetingHelper
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

### Manual Setup

If you prefer to set up manually:

```bash
# 1. Install & start Screenpipe
# Download from https://screenpi.pe/download or:
brew install screenpipe  # macOS
screenpipe record        # start recording

# 2. Add MCP server to Claude Code
claude mcp add screenpipe -- npx -y screenpipe-mcp

# 3. Install the meeting summary pipe
mkdir -p ~/.screenpipe/pipes/meeting-summary-zh
cp pipes/meeting-summary-zh/pipe.md ~/.screenpipe/pipes/meeting-summary-zh/

# 4. Start Claude Code
claude
```

### Usage

```bash
# Start Claude Code (with Screenpipe running in background)
claude

# During a meeting:
> What's being discussed right now?
> What did the last speaker say about the deadline?
> Show me what's on the shared screen

# After a meeting:
> Generate a summary of my last meeting
> What action items came out of the meeting?
> What did we decide about the budget?

# Meeting history:
> List today's meetings
> Find when we discussed the new feature last week
```

### Health Check

**macOS / Linux:**
```bash
bash scripts/health-check.sh
```

**Windows (PowerShell):**
```powershell
powershell -File scripts\health-check.ps1
```

## Platform Setup

### macOS

Grant these permissions to Screenpipe (System Settings > Privacy & Security):
1. **Screen Recording** — required for screen capture and meeting detection
2. **Accessibility** — required for meeting detection (UI element scanning)
3. **Microphone** — required for mic audio capture

System audio is captured via ScreenCaptureKit (macOS 13+) — no virtual audio device needed.

### Windows

1. **Microphone** access (Settings > Privacy > Microphone)
2. System audio captured via WASAPI Loopback — no extra config needed

## Speaker Management

Screenpipe identifies speakers by voice embeddings. First-time speakers start as "Speaker 0", "Speaker 1", etc.

### Name your speakers
```bash
# In Claude Code:
> List unnamed speakers
> Name Speaker 2 as "Alice Chen"
```

Once named, all past and future transcripts from that voice show the name. The mapping persists across meetings.

### Calendar-assisted identification
If your calendar is connected to Screenpipe, meeting attendee names are automatically used to constrain speaker clustering. This improves accuracy — the system knows how many speakers to expect.

### Merge duplicates
If the same person appears as multiple speaker IDs:
```bash
> Merge Speaker 3 into Speaker 1 (they're the same person)
```

## Q&A Logging

Every meeting Q&A session is automatically logged to `~/.meeting-helper/qa-log/YYYY-MM-DD.md`. This creates a searchable history of what you asked about each meeting and what you learned.

## Mixed-Language Retranscription

If Chinese-English mixed transcription quality is poor for specific segments, you can retranscribe after the meeting:
```bash
# In Claude Code:
> The transcript from 2:00-2:30 PM seems garbled, can you retranscribe it?
```

Claude will use the retranscription API with bilingual hints to improve accuracy.

## Known Limitations

- **Mixed-language transcription**: Whisper detects language per ~30-second chunk, not per sentence. Mid-sentence code-switching (e.g., "Let's discuss the 技术方案") may produce errors in that chunk
- **Speaker identification**: Remote meeting participants sharing one audio channel may not be individually identified. Local microphone speaker is reliably identified
- **Long meetings**: Summaries for meetings > 1 hour use chunked processing (30-min segments)

## Project Structure

```
meeting-helper/
├── CLAUDE.md                           # Claude Code instructions for meeting Q&A
├── README.md                           # This file
├── config/
│   └── screenpipe-settings.json        # Recommended Screenpipe settings
├── pipes/
│   └── meeting-summary-zh/
│       └── pipe.md                     # Custom meeting summary pipe (中英)
├── scripts/
│   ├── install.sh                      # macOS/Linux installer
│   ├── install.ps1                     # Windows installer
│   ├── setup-mcp.sh                    # MCP configuration for Claude Code
│   ├── health-check.sh                 # Verify all components (macOS/Linux)
│   └── health-check.ps1                # Verify all components (Windows)
├── docs/
│   └── ecl/
│       └── meeting-helper.yaml         # Planning document (ECL)
├── screenpipe/                         # Screenpipe source (reference, gitignored)
└── meetily/                            # Meetily source (reference, gitignored)
```

## Privacy

- All audio and screen data is stored locally on your machine (in `~/.screenpipe/`)
- Meeting transcripts are processed locally via Whisper
- When using Claude for summaries/Q&A, transcript data is sent to Anthropic's API
- For sensitive meetings, consider using a local LLM (Ollama) instead
- Check your jurisdiction's recording consent laws before use
- Many corporate environments prohibit meeting recording — verify your company's policy

## License

MIT
