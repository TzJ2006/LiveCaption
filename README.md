# LiveCaption

English | [中文](README.zh.md)

**LiveCaption** is a lightweight, multi-backend speech-to-text framework for macOS. Capture the microphone, system audio, or both; pick an ASR backend (Apple Speech, local Sherpa-ONNX, or Hugging Face); show live captions; and save transcripts in the project directory.

Meetings are a natural fit — Zoom/Teams/Meet system audio + your mic — but the same stack works for lectures, videos, language practice, or any live audio you want as text.

## Features

- Capture the microphone, system audio, or `auto` — both channels captured, one recognized
- Pluggable ASR: `apple` · `sherpa` · `hf` · `hf-stream`
- Separate paths for offline and streaming Hugging Face models, so each runs the way it was built
- Switch ASR model from the caption bar dropdown while captions keep running — no restart
- One caption pane, always — `auto` gates the two channels into it and labels each line
- Local Sherpa-ONNX bilingual Chinese-English streaming recognition
- Apple Speech realtime recognition and audio-file transcription
- Selectable, copyable, scrollable, hideable floating caption window
- One transcript for `auto`, every line tagged with the channel (and the detected language)
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

```bash
cd /path/to/LiveCaption
```

Recommended for meetings / dual audio (local Sherpa):

```bash
bash scripts/start.sh --source auto --asr sherpa
```

`auto` is the default source: both channels are captured, but only the one that is talking is
recognized — one recognizer instead of two, with every caption still tagged `(speaker)` or
`(microphone)`.

On first run, the bilingual INT8 model downloads automatically. Dependencies, models, caches, and temp files stay inside the project; after install, recognition works offline.

Stop:

```bash
bash scripts/stop.sh
```

## Common Commands

```bash
# Default: auto (both channels captured, one recognized) + Apple Speech
bash scripts/start.sh

# Auto on any backend: prefer speaker when active, microphone when quiet, one pane, labelled lines
bash scripts/start.sh --source auto --asr sherpa

# Apple Speech on the same gated pane
bash scripts/start.sh --source auto --asr apple

# System audio only
bash scripts/start.sh --source system --asr sherpa

# Microphone only
bash scripts/start.sh --source mic --asr sherpa

# Apple Speech English
bash scripts/start.sh --source mic --asr apple --language en-US

# A cache-aware streaming Hugging Face model (partial captions mid-sentence)
bash scripts/start.sh --source auto --asr hf-stream \
  --hf-model nvidia/nemotron-3.5-asr-streaming-0.6b --language auto

# Show levels and save debug WAVs
bash scripts/start.sh --source auto --asr sherpa --debug

# Window size / opacity
bash scripts/start.sh --source auto --asr sherpa --height 160 --opacity 0.85
```

Main options:

| Option | Values | Default |
| --- | --- | --- |
| `--source` | `mic`, `system`, `auto` (`both` is retired → `auto`) | `auto` |
| `--asr` | `apple`, `sherpa`, `hf`, `hf-stream` | `apple` |
| `--hf-model` | Hugging Face model id (required by `--asr hf` / `hf-stream`) | — |
| `--hf-models` | extra model ids for the caption bar dropdown, comma separated | — |
| `--language` | `auto`, or e.g. `zh-CN` / `en-US` (see Mixed-Language Meetings) | `zh-CN` |
| `--output-dir` | transcript output directory | `transcripts/` |
| `--height` | caption window height | `120` |
| `--opacity` | background opacity | `0.75` |
| `--debug` | show levels and save WAVs | off |

## Caption Window

- One caption pane in every mode, on every backend. The side-by-side speaker|microphone columns
  are retired: `--source both` is still accepted and resolves to `auto`
- `--source auto`: smart-gated pane, each line prefixed with the channel it came from; both
  channels share the main transcript, tagged the same way
- `--source mic` / `--source system`: the same pane fed by that one channel
- Drag handle (left of the model dropdown): move the window anywhere on screen
- Model dropdown: switch the ASR model without restarting (see below)
- `Hide` / `Show`: collapse to a pill holding just the control bar, or restore captions
- `Quit`: stop LiveCaption
- Select text, then `Cmd+C` (Windows: `Ctrl+C`): copy selection
- Same shortcut with no selection: copy all captions
- Mouse scroll: browse caption history

## Mixed-Language Meetings

`--language` does not reach every backend. `sherpa`, `hf` and `wsl-vllm` detect the language
themselves and take no flag; `apple` needs a real locale, and **`--language auto` on Apple Speech
silently means `zh-CN`** — there is no automatic detection there.

`--asr hf-stream` is the one that does detect per line. On `--language auto` the checkpoint tags
each utterance with the locale it heard, so a bilingual meeting labels itself. On an English →
Chinese → English clip:

| `--language` | English passages | Chinese passages |
| --- | --- | --- |
| `auto` | transcribed, tagged `en-US` | transcribed, tagged `zh-CN` |
| `zh-CN` | **lost entirely** | transcribed (slightly more accurate than `auto`) |
| `en-US` | transcribed | **lost entirely** |

So for a meeting that switches languages, `auto` is not a refinement — pinning the wrong language
does not degrade the other one, it drops it. Pin a language only when the whole meeting is in it.

The detected locale is written into the transcript, not onto the captions:

```text
[14:03:21] (speaker) [en-US] Hello everyone, welcome to the meeting.
[14:03:29] (microphone) [zh-CN] 大家好，欢迎参加今天的会议。
```

Detection is per utterance, so a sentence that switches language mid-way is transcribed correctly
in both but carries a single tag — whichever language it ended in.

## Offline and Streaming Models Take Different Paths

A Hugging Face checkpoint is one of two things, and LiveCaption runs each on its own worker
instead of forcing both through one:

| | `--asr hf` (offline) | `--asr hf-stream` (streaming) |
| --- | --- | --- |
| Flag | `--asr hf` | `--asr hf-stream` |
| Model | any seq2seq / CTC checkpoint — Whisper, Qwen3-ASR | cache-aware RNNT — Nemotron ASR streaming |
| How audio is fed | cut into `--chunk-seconds` clips, each decoded on its own | one recognition stays open, fed the chunk size the model was trained on |
| Captions | one per clip, always final | partial text mid-sentence, committed at the model's own punctuation |
| Between chunks | encoder state thrown away | encoder cache reused, so nothing is recomputed |
| Needs | `transformers` (+ `qwen-asr` for Qwen3-ASR) | `transformers >= 5.13`, and a GPU to keep up live |

Running a streaming checkpoint on the offline path works but wastes it — you get the accuracy of
a model built for low latency with none of the low latency. Both workers say so on stderr if the
model and the path do not match.

`qwen-asr` pins `transformers==4.57.6` while the streaming worker needs `>= 5.13`, so the two
cannot share one interpreter. `scripts/setup-hf-stream.sh` builds that second environment inside
the project at `.build/stream-env`, and `start.sh` runs it for you the first time a streaming
model is in reach. Override it with `--hf-stream-python` if you keep your own.

## GPU

Both Hugging Face workers pick a device automatically: CUDA, then `mps` (the Apple Silicon GPU),
then the CPU. Nothing to configure — the worker prints its choice as `ASR device: mps` on stderr
(`logs/subtitle.log`) and shows it in the caption bar's `... ready (mps)` message.

This is not a nice-to-have for the streaming path. Measured on this project's Mac, 44 seconds of
audio through `nvidia/nemotron-3.5-asr-streaming-0.6b`:

| Device | Wall time | vs realtime |
| --- | --- | --- |
| `mps` | 16 s | 2.7x faster — comfortably live |
| `cpu` | 147 s | 3.3x slower — the backlog grows forever and captions never catch up |

To force a device — say an operation is missing on MPS in your torch build:

```bash
LIVECAPTION_DEVICE=cpu bash scripts/start.sh --source auto --asr hf-stream \
  --hf-model nvidia/nemotron-3.5-asr-streaming-0.6b
```

## Switching Models Live

The dropdown between the drag handle and `Hide` lists every backend the host can run — macOS
`Apple Speech` and `Sherpa-ONNX`, Windows `Sherpa-ONNX` and `WSL vLLM` — plus each Hugging Face
model id from `--hf-model` and `--hf-models`, tagged `(streaming)` or `(chunked)` so you can see
which path it takes:

```bash
bash scripts/start.sh --source auto --asr sherpa \
  --hf-models Qwen/Qwen3-ASR-0.6B,openai/whisper-large-v3-turbo
```

Ids with `streaming` in them go to the streaming path; anything else is chunked. Prefix an id
with `stream:` or `offline:` to override that.

Picking an entry stops the running recognizer and starts the new one in place; capture, the
transcript files and the caption history all keep going, and the pill keeps the dropdown so you
can switch while collapsed. Progress shows up in the caption area as `Switching to ...`, then
`... ready`. A model that cannot start (missing dependency, model not downloadable, Speech
Recognition permission refused) reports the failure there instead of quitting — pick another
entry to carry on. The pane never changes shape across a switch: the layout is fixed by
`--source` alone, so captions do not jump when the model does.

Hugging Face models download into `models/hf/` inside the project on first use. Set `HF_HOME`
yourself if you would rather share a cache with other tools.

**The dropdown only lists models you named.** A bare `bash scripts/start.sh` passes no model, so
the menu holds just the built-in backends. Put the ids on the command line, or in `config.json`:

```json
{
  "source": "auto",
  "asr": "hf-stream",
  "hf-model": "nvidia/nemotron-3.5-asr-streaming-0.6b",
  "hf-models": ["Qwen/Qwen3-ASR-0.6B", "openai/whisper-large-v3-turbo"],
  "language": "auto"
}
```

Both hosts read `config.json` from the project root (`--config <path>` points elsewhere).
Precedence is built-in default < `config.json` < environment variable < command-line flag, so a
flag you type always wins. Copy `config.example.json` to start.

## File Layout

All runtime files live under the project directory:

```text
LiveCaption/
├── scripts/                 # start, stop, and setup scripts
├── src/
│   ├── swift/               # macOS host and Apple Speech tools
│   └── python/              # ASR workers and transcript helpers
├── .build/                  # build artifacts, Python deps, caches
│   └── stream-env/          # transformers >= 5.13 venv for the streaming worker
├── models/                  # local Sherpa models
│   └── hf/                  # Hugging Face cache (HF_HOME)
├── transcripts/             # caption text
│   ├── YYYY-MM-DD.txt       # microphone; also both channels under auto, each line labelled
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

## ASR Backends

| Mode | Best for | Notes |
| --- | --- | --- |
| `sherpa` | Dual-source offline captions (e.g. meetings) | Recommended; true streaming, bilingual, fully local after install |
| `apple` | Single source, smart-gated dual source, manual file transcription | System-native; realtime tasks rotate every ~50s; may use Apple online speech |
| `hf` | Offline Hugging Face models (Whisper, Qwen3-ASR) | Experimental; you manage deps and models; captions arrive one block at a time |
| `hf-stream` | Cache-aware streaming Hugging Face models (Nemotron ASR streaming) | Experimental; needs `transformers >= 5.13` and realistically a GPU |

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
