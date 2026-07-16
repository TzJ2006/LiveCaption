# MeetingHelper

English | [中文](README.zh.md)

MeetingHelper is a lightweight macOS live-subtitle tool. It can capture the microphone and current system audio at the same time, show captions at the bottom of the screen, and save transcripts plus debug audio inside the project directory.

## Features

- Capture microphone, system audio, or both
- Apple dual-source smart gating (merged); Sherpa/HF dual-source side-by-side panes
- Local Sherpa-ONNX bilingual Chinese-English streaming recognition
- Apple Speech realtime recognition and audio-file transcription
- Selectable, copyable, scrollable, hideable floating caption window
- Separate dual-source transcripts for Sherpa/HF; merged transcript for Apple dual-source
- Debug mode saves WAVs for capture checks and offline transcription
- Auto-installs Sherpa models into the project directory when missing

## Requirements

- macOS 13 or later
- Xcode Command Line Tools (`xcrun swiftc`)
- Python 3 (Sherpa / Hugging Face modes only)
- Microphone permission (when using `mic`)
- Screen Recording permission (when capturing system audio)
- Speech Recognition permission (when using Apple Speech)

## Quick Start

Enter the project directory:

```bash
cd /path/to/MeetingHelper
```

Recommended: local Sherpa, recognizing system audio and microphone together:

```bash
bash scripts/start.sh --source both --asr sherpa
```

On first run, the bilingual INT8 model downloads automatically. Dependencies, models, caches, and temp files stay inside MeetingHelper; after install, recognition works offline.

Stop:

```bash
bash scripts/stop.sh
```

## Common Commands

```bash
# Default: microphone + Apple Speech
bash scripts/start.sh

# Apple Speech merges system/microphone: prefer system when active, mic when quiet
bash scripts/start.sh --source both --asr apple

# System audio only
bash scripts/start.sh --source system --asr sherpa

# Microphone only
bash scripts/start.sh --source mic --asr sherpa

# Both sources, left/right panes (Sherpa/HF)
bash scripts/start.sh --source both --asr sherpa

# Apple Speech English
bash scripts/start.sh --source mic --asr apple --language en-US

# Show levels and save debug WAVs
bash scripts/start.sh --source both --asr sherpa --debug

# Window size / opacity
bash scripts/start.sh --source both --asr sherpa --height 160 --opacity 0.85
```

Main options:

| Option | Values | Default |
| --- | --- | --- |
| `--source` | `mic`, `system`, `both` | `mic` |
| `--asr` | `apple`, `sherpa`, `hf` | `apple` |
| `--language` | e.g. `zh-CN`, `en-US` | `zh-CN` |
| `--output-dir` | transcript output directory | `transcripts/` |
| `--height` | caption window height | `120` |
| `--opacity` | background opacity | `0.75` |
| `--debug` | show levels and save WAVs | off |

## Caption Window

- `--source both --asr apple`: smart-gated merge of both streams; single meeting pane + main transcript
- `--source both` with Sherpa/HF: left = speaker/system, right = microphone
- `Hide` / `Show`: collapse or restore captions
- `Quit`: stop MeetingHelper
- Select text, then `Cmd+C`: copy selection
- `Cmd+C` with no selection: copy all captions in the current pane
- Mouse scroll: browse caption history

## File Layout

All runtime files live under the project directory:

```text
MeetingHelper/
├── scripts/                 # start, stop, and setup scripts
├── src/
│   ├── swift/               # macOS host and Apple Speech tools
│   └── python/              # ASR workers and transcript helpers
├── .build/                  # build artifacts, Python deps, caches
├── models/                  # local Sherpa models
├── transcripts/             # caption text
│   ├── YYYY-MM-DD.txt       # microphone, or merged Apple both transcript
│   └── YYYY-MM-DD-sys.txt   # speaker/system
├── debug-audio/             # debug WAVs
└── logs/
    ├── subtitle.log
    ├── subtitle-stop.log
    └── subtitle.pid
```

These runtime directories are in `.gitignore`.

## Transcribe Audio Files

Use Apple Speech to transcribe a WAV (or other AVFoundation-supported file):

```bash
bash scripts/transcribe.sh "debug-audio/example.wav" --language en-US
```

Write the result to a file:

```bash
bash scripts/transcribe.sh "debug-audio/example.wav" \
  --language en-US \
  --output "transcripts/example.txt"
```

This command exits after the file is processed; it does not keep running.

## ASR Choices

| Mode | Best for | Notes |
| --- | --- | --- |
| `sherpa` | Dual-source offline meeting captions | Recommended; true streaming, bilingual, fully local after install |
| `apple` | Single source, smart-gated dual source, manual file transcription | System-native; realtime tasks rotate every ~50s; may use Apple online speech |
| `hf` | Custom Hugging Face models | Experimental; you manage deps and models |

## Local LLM

`src/python/query_transcript.py` can send a transcript to an OpenAI-compatible local API (e.g. Ollama):

```bash
python3 src/python/query_transcript.py \
  transcripts/2026-07-10.txt \
  "Summarize the meeting decisions and action items"
```

Default endpoint: `http://localhost:11434/v1`, default model: `llama3.1`. Override with `LOCAL_LLM_BASE_URL`, `LOCAL_LLM_MODEL`, and `LOCAL_LLM_API_KEY`.

## Full Tutorial

Permissions, English recognition, debug audio, and troubleshooting: [tutorial.md](tutorial.md) · [中文教程](tutorial.zh.md).
