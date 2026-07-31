# AGENTS.md

LiveCaption is a lightweight multi-backend live speech-to-text framework: it captures microphone
and/or system (speaker) audio, streams it to a pluggable ASR worker, shows a floating caption
overlay, and appends transcripts under `transcripts/`. Two hosts share one worker protocol: a Swift
host for macOS (`src/swift/LiveSubtitle.swift` — AVAudioEngine, ScreenCaptureKit, Apple Speech) and
a pure-Python host for Windows (`src/python/win_host.py` — WASAPI loopback via pyaudiowpatch,
tkinter overlay). No build system beyond `pip` and (macOS only) `xcrun swiftc`.

## Commands

- Setup (Windows): `pip install -r requirements.txt` (numpy, pyaudiowpatch, qwen-asr, transformers, CUDA torch via the cu128 extra index)
- Run (Windows): `scripts/start.bat --source both --asr hf --hf-model Qwen/Qwen3-ASR-0.6B [--record]` — thin wrapper around `python src/python/win_host.py`; stop with `scripts/stop.bat`
- Run (macOS): `bash scripts/start.sh --source both --asr sherpa` — compiles the Swift host with `xcrun swiftc`, backgrounds it via nohup; stop with `bash scripts/stop.sh`
- Sherpa setup (macOS): `bash scripts/setup-sherpa.sh` — auto-invoked by start.sh; installs sherpa-onnx into `.build/pydeps` and downloads the bilingual zh-en model into `models/`
- File transcription (macOS): `bash scripts/transcribe.sh <file.wav> --language en-US [--output out.txt]` (Apple Speech, exits when done)
- Test: `python src/python/win_host.py --self-test` — the only automated test (prints `self-test ok`); there is no pytest/unittest suite
- Transcript LLM query: `python3 src/python/query_transcript.py transcripts/<date>.txt "prompt"` (OpenAI-compatible local API, default Ollama at `http://localhost:11434/v1`; override with `LOCAL_LLM_BASE_URL` / `LOCAL_LLM_MODEL` / `LOCAL_LLM_API_KEY`)

No linter or formatter is configured.

## Architecture

```text
scripts/                  start/stop (.sh for macOS, .bat for Windows), setup-sherpa.sh, transcribe.sh
src/swift/
  LiveSubtitle.swift      macOS host: capture, Apple Speech ASR, caption window, transcript writes;
                          spawns the Python workers for --asr hf/sherpa
  TranscribeAudio.swift   one-shot Apple Speech file transcription (built by transcribe.sh)
src/python/
  win_host.py             Windows host: WASAPI mic + speaker-loopback capture, tkinter overlay,
                          transcript writes; spawns an ASR worker subprocess
  hf_asr_worker.py        chunked Hugging Face ASR (generic pipeline; qwen-asr for Qwen3-ASR models)
  sherpa_asr_worker.py    streaming sherpa-onnx bilingual zh-en; imports deps from .build/pydeps
  qwen_stream_worker.py   true-streaming Qwen3-ASR via vLLM; Linux/WSL only (--asr wsl-vllm on Windows)
  transcribe_sherpa.py    offline WAV transcription with the local Sherpa model
  align_transcript.py     word/char timestamps via Qwen3-ForcedAligner (speech only, <= 5 min)
  query_transcript.py     send a transcript to a local OpenAI-compatible LLM
config.example.json       template for config.json (local, gitignored)
transcripts/ recordings/ models/ logs/ debug-audio/ .build/   runtime output, all gitignored
```

Data flow: host -> worker stdin as NDJSON
`{"type":"audio","source":"mic|sys","sampleRate":N,"pcmFloat32":"<base64 float32 mono>"}`;
worker -> stdout `{"source":...,"text":...,"final":true|false}` plus a `{"status":"ready"}` event.
Events with `final:true` are appended to `transcripts/YYYY-MM-DD.txt` (mic / merged) and
`transcripts/YYYY-MM-DD-sys.txt` (system audio).

Config: `win_host.py` loads `config.json` values as argparse defaults, so CLI flags always win.

## Conventions

- Python: 4-space indent, snake_case, short single-purpose modules with a usage docstring on top;
  hosts stick to stdlib + numpy, heavy ML deps are confined to the worker processes
- `# ponytail:` comments mark deliberate minimalism decisions — read them before "fixing" the code
- Docs come in bilingual pairs (README.md / README.zh.md, tutorial.md / tutorial.zh.md); keep both in sync
- ASR choices differ per host: Windows `--asr hf|sherpa|wsl-vllm`, macOS `--asr apple|hf|sherpa`

## Gotchas

- `scripts/start.bat` / `win_host.py` run the caption overlay in the foreground and never exit until
  Quit/Esc; `scripts/stop.bat` force-kills the host and any worker. On macOS, start.sh backgrounds
  the Swift binary and returns (PID in `logs/subtitle.pid`).
- On non-macOS, `bash scripts/start.sh` execs win_host.py in the foreground. Under WSL it refuses:
  WSL cannot capture Windows audio and is only an ASR backend (`--asr wsl-vllm` runs
  qwen_stream_worker.py through `wsl <wsl-python>`).
- `requirements.txt` needs its `--extra-index-url .../whl/cu128` line for CUDA torch on Windows, and
  the `numba>=0.59` / `llvmlite>=0.42` floors stop resolvers backtracking to wheels whose build
  fails — do not remove them.
- Sherpa deps live in `.build/pydeps` (installed by setup-sherpa.sh), not site-packages;
  sherpa_asr_worker.py and win_host.py add that path themselves.
- win_host.py shutdown deliberately calls `os._exit(0)` and skips `pa.terminate()` / stdin close —
  those can deadlock on live streams (see the comment at the end of `main()`).
- `config.json` is gitignored local config and may hold machine-specific paths (e.g. `wsl-python`);
  never commit it. `transcripts/` and `recordings/` hold private meeting data.
- Apple Speech realtime tasks rotate roughly every 50 s; the restart blip is expected, not a bug.
