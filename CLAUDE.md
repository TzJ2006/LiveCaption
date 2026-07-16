# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

LiveCaption is a lightweight multi-backend speech-to-text framework for macOS: it captures microphone and/or system audio, routes audio through a chosen ASR backend (Apple / Sherpa / HF), shows real-time subtitles in a floating window, and writes transcripts to `transcripts/`. Meetings are a common use case. See `AGENTS.md` for the same constraints in short form.

## Commands

There is no test suite or linter. The Swift host is compiled on every start by `start.sh` via `xcrun swiftc src/swift/LiveSubtitle.swift -o .build/live-subtitle` — to verify a Swift change compiles, just run that command or `bash scripts/start.sh`.

```bash
bash scripts/start.sh                          # mic only, Apple Speech, zh-CN default
bash scripts/start.sh --source system          # system output only (no BlackHole needed)
bash scripts/start.sh --source both --debug    # also dumps WAVs to debug-audio/
bash scripts/start.sh --source both --asr sherpa
bash scripts/start.sh --asr hf --hf-model openai/whisper-small --source both
bash scripts/stop.sh

# One-off file transcription with Apple Speech
bash scripts/transcribe.sh debug-audio/example.wav --language en-US [--output out.txt]

# Local LLM Q&A over a transcript (OpenAI-compatible API, defaults to Ollama at localhost:11434)
python3 src/python/query_transcript.py transcripts/2026-07-07.txt "Summarize action items."
```

`start.sh` flags: `--source mic|system|both`, `--asr apple|hf|sherpa`, `--language`, `--hf-model`, `--output-dir`, `--opacity`, `--height`, `--debug`. Each is also settable via env vars (`SOURCE`, `ASR`, `LANGUAGE`, etc.). It's a singleton: refuses to start if `logs/subtitle.pid` points to a live process.

## Architecture

Single Swift host process with optional Python ASR workers spawned as subprocesses:

- `src/swift/LiveSubtitle.swift` — the entire app: mic capture (`AVAudioEngine`), system audio capture (`ScreenCaptureKit`, macOS 13+), Apple Speech ASR, the floating subtitle window, and transcript file writes. Runs in background via `nohup`; logs to `logs/subtitle.log`, PID in `logs/subtitle.pid`.
  - macOS Speech only supports ONE live recognition task per process, so `--source both` with Apple ASR spawns a second instance of the same binary in hidden `--speech-worker` mode (headless, same NDJSON protocol as the Python workers) to handle the system source. Don't move both sources' Apple recognition back into one process — they immediately kill each other with error 1110.
- `src/python/hf_asr_worker.py` — optional Hugging Face ASR worker (`--asr hf`). Swift streams NDJSON audio frames to it over stdin and reads NDJSON transcript events back.
- `src/python/sherpa_asr_worker.py` — optional sherpa-onnx bilingual ASR worker (`--asr sherpa`), same NDJSON protocol. Deps installed with `python3 -m pip install --target .build/pydeps sherpa-onnx`.
- `src/python/query_transcript.py` — standalone helper; reads transcript files and calls an OpenAI-compatible local API (`LOCAL_LLM_BASE_URL`/`LOCAL_LLM_MODEL`/`LOCAL_LLM_API_KEY`).
- `src/swift/TranscribeAudio.swift` + `scripts/transcribe.sh` — one-shot file transcription with Apple Speech.

Data layout: `transcripts/YYYY-MM-DD.txt` (mic), `transcripts/YYYY-MM-DD-sys.txt` (system), `debug-audio/*.wav` (only with `--debug`).

## Constraints

- Do NOT reintroduce Screenpipe/MCP into the default path — that legacy stack is archived under `archive/` and stays there.
- Keep the realtime path small: audio capture, ASR, subtitles, transcript writes. Resist adding features to the Swift host.
- Requires macOS permissions: Microphone, Screen Recording (for system audio), Speech Recognition. Missing permissions fail at runtime, not compile time — check `logs/subtitle.log`.
