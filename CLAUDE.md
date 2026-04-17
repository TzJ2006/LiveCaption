# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MeetingHelper is a real-time AI meeting assistant. It has two layers:
1. **Screenpipe** (background service) — captures audio/screen, runs Whisper ASR, stores to SQLite, exposes 27 MCP tools
2. **Local subtitle overlay** (`scripts/live-subtitle.py`) — a PyObjC floating window that streams microphone audio through one of 6 ASR backends and writes transcripts to disk

Claude Code acts as the interaction layer: querying meeting content via MCP tools or reading transcript files directly.

## Architecture

```
Microphone ──► live-subtitle.py (ASR) ──► ~/.meeting-helper/transcripts/YYYY-MM-DD.txt
                  ▲
System Audio ──┤  (--source both)     ──► ~/.meeting-helper/transcripts/YYYY-MM-DD-sys.txt
               │
System Audio ──► Screenpipe (Whisper) ──► SQLite DB ──► MCP tools ──► Claude Code
Screen ──────┘
```

- **Subtitle overlay**: real-time, low-latency (~2s chunks), writes `[HH:MM:SS] text` lines to transcript files
- **Screenpipe MCP**: richer data (speaker diarization, OCR, meeting detection, UI elements), higher latency (~30s)
- Both can run simultaneously; they serve different purposes

### ASR Backends (live-subtitle.py `--model` flag)

| Backend | Engine | Streaming | Languages | Notes |
|---------|--------|-----------|-----------|-------|
| `zipformer` (default) | Sherpa-ONNX | true streaming | zh, en | Lowest latency |
| `paraformer` | Sherpa-ONNX | true streaming | zh, en | Alternative bilingual |
| `qwen3-asr` | torch | chunked | 52 | Requires torch + qwen-asr |
| `whisper` | faster-whisper | chunked | 99 | CTranslate2 int8, ~1.6GB |
| `moonshine` | MLX | event-driven | 8 | Apple Silicon optimized |
| `voxtral` | MLX | chunked | 13 | 4-bit quantized, ~3GB |

## Key Commands

```bash
# Setup
bash scripts/install.sh              # Full automated install (Node.js, Screenpipe, MCP, pipe)
bash scripts/setup-sherpa.sh         # Install Python deps + download Sherpa-ONNX models

# Start/stop subtitle overlay
bash scripts/start.sh                          # Default: zipformer, mic only
bash scripts/start.sh --source both            # Mic + system audio simultaneously
bash scripts/start.sh --source system          # System audio only (needs BlackHole)
bash scripts/start.sh --source both --system-device "BlackHole 2ch"  # Explicit device
bash scripts/start.sh --model whisper          # Use faster-whisper backend
bash scripts/start.sh --opacity 0.5 --height 200  # Customize UI
bash scripts/stop.sh                           # Stop subtitle window

# Health check (7 component checks)
bash scripts/health-check.sh

# MCP setup
claude mcp add screenpipe -- npx -y screenpipe-mcp
```

**Hotkey**: Cmd+Shift+S toggles subtitle window visibility.

## Key Data Locations

| Path | Content |
|------|---------|
| `~/.meeting-helper/transcripts/YYYY-MM-DD.txt` | Live transcripts — mic (`[HH:MM:SS] text` per line) |
| `~/.meeting-helper/transcripts/YYYY-MM-DD-sys.txt` | Live transcripts — system audio (when `--source both`) |
| `~/.meeting-helper/qa-log/YYYY-MM-DD.md` | Q&A session logs |
| `~/.meeting-helper/models/` | Sherpa-ONNX model files (zipformer, paraformer) |
| `~/.meeting-helper/subtitle.pid` | Running subtitle process PID |
| `~/.meeting-helper/subtitle.log` | Subtitle process log |
| `~/.screenpipe/db/` | Screenpipe SQLite database |
| `~/.screenpipe/pipes/meeting-summary-zh/` | Custom summary pipe (installed from `pipes/`) |

## Meeting Assistant Behavior

You are a meeting assistant. When interacting with meeting content, follow these rules:

### Querying Transcripts

1. **Always read the transcript file** before answering meeting questions. Replace `YYYY-MM-DD` with today's date.
2. If the file doesn't exist, tell the user to run `bash scripts/start.sh`.
3. For long meetings, use `offset` and `limit` to read only the latest portion.
4. Use `Grep` to search for keywords across transcript files.

### Response Language

Use the meeting's language. If the meeting is in Chinese, respond in Chinese. If mixed, use both naturally.

### Summary Structure

- 一句话概述 / One-line summary
- 主要讨论点 / Key discussion points
- 决策事项 / Decisions made
- 行动项 / Action items (with owners and deadlines if mentioned)
- 未解决问题 / Open questions

### Q&A Logging

After answering substantive meeting questions, generating summaries, or extracting action items, append a log entry to `~/.meeting-helper/qa-log/YYYY-MM-DD.md`:

```markdown
## HH:MM — [Meeting: name or "Unknown"] — [Q&A / Summary / Action Items]

**Questions asked:**
1. [User's question]
   → [Brief answer summary, 1-2 sentences]

**Key findings:**
- [Most important thing learned]

---
```

Create the directory with `mkdir -p ~/.meeting-helper/qa-log` if it doesn't exist. Log AFTER answering, not before. Never log raw transcripts.

## Known Limitations

- **No speaker ID in local ASR**: The subtitle overlay does not identify individual speakers (Screenpipe MCP has diarization)
- **Chinese-English code-switching**: Whisper detects language per ~30s chunk, not per sentence — mid-sentence mixing degrades accuracy
- **Long meetings (>1hr)**: May exceed context window; use time-bounded MCP queries or chunked summarization
- **macOS permissions required**: Microphone (required), Screen Recording and Accessibility (for Screenpipe screen capture)
- **System audio requires virtual device**: `--source system/both` needs BlackHole (or equivalent) with Multi-Output Device configured in Audio MIDI Setup
- **Dual mode doubles memory**: `--source both` loads two ASR model instances; recommend zipformer/paraformer for dual mode
