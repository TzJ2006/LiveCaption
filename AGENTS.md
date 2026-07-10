# AGENTS.md

MeetingHelper is now a minimal macOS live subtitle tool.

## Current Architecture

- Swift host: `src/swift/LiveSubtitle.swift`
  - Captures microphone with `AVAudioEngine`
  - Captures current system output with `ScreenCaptureKit`
  - Uses Apple Speech for default ASR
  - Owns floating subtitle UI and transcript file writes
- Optional Python HF worker: `src/python/hf_asr_worker.py`
  - Used only with `--asr hf`
  - Receives NDJSON audio frames from Swift
  - Returns NDJSON transcript events
- Optional Python LLM helper: `src/python/query_transcript.py`
  - Reads transcript files
  - Calls an OpenAI-compatible local API

## Commands

```bash
bash scripts/start.sh
bash scripts/start.sh --source system
bash scripts/start.sh --source both
bash scripts/start.sh --source both --debug
bash scripts/start.sh --source both --asr sherpa
bash scripts/start.sh --asr hf --hf-model openai/whisper-small --source both
bash scripts/stop.sh
```

## Data

- `transcripts/YYYY-MM-DD.txt`: microphone transcript
- `transcripts/YYYY-MM-DD-sys.txt`: system output transcript
- `debug-audio/YYYY-MM-DD-HHMMSS-*.wav`: local debug audio captures
- `logs/subtitle.pid`: running process PID
- `logs/subtitle.log`: runtime log

## Notes

- Do not reintroduce Screenpipe/MCP into the default path.
- Archived legacy files live under `archive/`.
- Keep the realtime path small: audio capture, ASR, subtitles, transcript writes.
