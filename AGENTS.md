# AGENTS.md

LiveCaption is a lightweight multi-backend live speech-to-text framework: it captures microphone
and/or system (speaker) audio, streams it to a pluggable ASR worker, shows a floating caption
overlay, and appends transcripts under `transcripts/`. Two hosts share one worker protocol: a Swift
host for macOS (`src/swift/LiveSubtitle.swift` — AVAudioEngine, ScreenCaptureKit, Apple Speech) and
a pure-Python host for Windows (`src/python/win_host.py` — WASAPI loopback via pyaudiowpatch,
tkinter overlay). No build system beyond `pip` and (macOS only) `xcrun swiftc`.

## Commands

- Setup (Windows): `pip install -r requirements.txt` (numpy, pyaudiowpatch, qwen-asr, transformers, CUDA torch via the cu128 extra index)
- Run (Windows): `scripts/start.bat --source auto --asr hf --hf-model Qwen/Qwen3-ASR-0.6B [--record]` — thin wrapper around `python src/python/win_host.py`; stop with `scripts/stop.bat`
- Run (macOS): `bash scripts/start.sh --source auto --asr sherpa` — compiles the Swift host with `xcrun swiftc`, backgrounds it via nohup; stop with `bash scripts/stop.sh`
- Sherpa setup (macOS): `bash scripts/setup-sherpa.sh` — auto-invoked by start.sh; installs sherpa-onnx into `.build/pydeps` and downloads the bilingual zh-en model into `models/`
- Streaming setup: `bash scripts/setup-hf-stream.sh` — auto-invoked by start.sh when a streaming model is in reach; builds `.build/stream-env` (venv with `--system-site-packages`) holding transformers >= 5.13
- File transcription (macOS): `bash scripts/transcribe.sh <file.wav> --language en-US [--output out.txt]` (Apple Speech, exits when done)
- Test: `python src/python/win_host.py --self-test` — the only automated test (prints `self-test ok`); there is no pytest/unittest suite. It is a plain `assert` block covering downmix/RMS, the `AudioGate` handover and resampler drift, `hf_choice()`/`model_choices()`, and the overlay's text model, pill collapse and drag clamp — it builds a real hidden `Tk` window, so it needs a display
- Transcript LLM query: `python3 src/python/query_transcript.py transcripts/<date>.txt "prompt"` (OpenAI-compatible local API, default Ollama at `http://localhost:11434/v1`; override with `LOCAL_LLM_BASE_URL` / `LOCAL_LLM_MODEL` / `LOCAL_LLM_API_KEY`)

No linter or formatter is configured.

## Architecture

```text
scripts/                  start/stop (.sh for macOS, .bat for Windows), setup-sherpa.sh,
                          setup-hf-stream.sh, transcribe.sh, config_to_env.py
src/swift/
  LiveSubtitle.swift      macOS host: capture, Apple Speech ASR, caption window, transcript writes;
                          spawns the Python workers for --asr hf/sherpa
  TranscribeAudio.swift   one-shot Apple Speech file transcription (built by transcribe.sh)
src/python/
  win_host.py             Windows host: WASAPI mic + speaker-loopback capture, tkinter overlay,
                          transcript writes; spawns an ASR worker subprocess
  hf_asr_worker.py        offline path: chunked Hugging Face ASR (generic pipeline; qwen-asr for
                          Qwen3-ASR); points HF_HOME at models/hf/ so weights land in the project
  hf_stream_worker.py     streaming path: cache-aware RNNT (AutoModelForRNNT + TextIteratorStreamer),
                          one live generate() per source; needs transformers >= 5.13
  sherpa_asr_worker.py    streaming sherpa-onnx bilingual zh-en; imports deps from .build/pydeps
  qwen_stream_worker.py   true-streaming Qwen3-ASR via vLLM; Linux/WSL only (--asr wsl-vllm on Windows)
  transcribe_sherpa.py    offline WAV transcription with the local Sherpa model
  align_transcript.py     word/char timestamps via Qwen3-ForcedAligner (speech only, <= 5 min)
  query_transcript.py     send a transcript to a local OpenAI-compatible LLM
config.example.json       template for config.json (local, gitignored)
transcripts/ recordings/ models/ logs/ debug-audio/ .build/   runtime output, all gitignored
```

Data flow: host -> worker stdin as NDJSON
`{"type":"audio","source":"mic|sys|auto","sampleRate":N,"pcmFloat32":"<base64 float32 mono>"}`;
worker -> stdout `{"source":...,"text":...,"final":true|false[,"language":"en-US"]}` plus a
`{"status":"ready"}` event. `language` is optional and only `hf_stream_worker.py` on
`--language auto` sends it; it reaches the transcript, never the captions.
Events with `final:true` are appended to `transcripts/YYYY-MM-DD.txt` (mic / auto) and
`transcripts/YYYY-MM-DD-sys.txt` (system audio). Workers key their state off the source string and
never interpret it, so `auto` needs no worker change.

One caption pane, always. `--source auto` (the default) captures both channels but recognizes only
the one that is talking — `AudioGate` / `class AudioGate` in the two hosts, kept identical: speaker
while its RMS is >= -45 dB, microphone after 0.6 s of quiet, resampled to 16 kHz with a per-channel
fractional cursor. The winning audio reaches the worker under the single label `auto` so the stream
never breaks (splitting it back into mic/sys would starve whichever side is quiet, and a streaming
recognizer needs that silence to end its sentence); the host remembers which channel each frame
came from and puts the name back on the caption. A line is credited to whichever channel opened it
and is not relabelled as it grows.

`--source both` — one recognizer per channel in two side-by-side columns — is retired. Both hosts
and start.sh still accept the value and resolve it to `auto` with a note on stderr, so older
`config.json` files keep working. Do not reintroduce a second pane: two recognizers writing one
window is exactly what the gate exists to avoid, and a single text view has one tail, so two live
partial lines would overwrite each other.

Caption window: `Overlay` (win_host.py) is a port of `SubtitleWindow` / `DragHandleView`
(LiveSubtitle.swift), and the two are kept in step by mirroring the Swift `static let` sizes as
class constants — `BAR_H`/`BTN_W`/`MODEL_W`/`HANDLE`/`GAP`/`PAD`, with `COLLAPSED_W` derived from
them rather than hardcoded. Those are macOS points; `_px()` scales them once against the display
DPI, so a layout change means editing the constant in both hosts, never a pixel literal. The window
spans the full screen width, the handle drags it, and Hide collapses it to a pill that keeps only
the control bar pinned to the window's bottom-right corner. `_clamp()` is `clampedOrigin()` flipped
into Tk's top-left origin: `MIN_VISIBLE` stays reachable so the window cannot be dragged away.

Model hot swap: the caption bar's dropdown (built from `--hf-model` + `--hf-models` plus the
host's built-in backends) stops the running recognizer and starts the new one without restarting
capture. Both hosts guard the swap with a generation counter so a retired worker's output cannot
reach the captions or the transcript, and a worker that dies only reports into the caption area
instead of ending the run.

Streaming vs offline Hugging Face models: two separate worker scripts, never one with a flag.
`--asr hf` chunks audio into self-contained clips (every caption `final:true`); `--asr hf-stream`
holds a cache-aware recognition open and emits `final:false` partials, committing on the model's
own sentence punctuation or `--finalize-seconds` of audio. Dropdown entries are classified by
`hfChoice()` / `hf_choice()` — kept identical in both hosts: a `stream:` or `offline:` prefix on
the id wins, otherwise `streaming` in the id picks the streaming path. The menu shows the result
as `(streaming)` / `(chunked)`, so a wrong guess is visible before it is used.

Config: `win_host.py` loads `config.json` values as argparse defaults. The Swift host parses CLI
flags only, so `scripts/start.sh` resolves the same file through `scripts/config_to_env.py`, which
prints `CFG_*` shell assignments for start.sh to eval. Precedence on both hosts is built-in default
< `config.json` < environment variable < CLI flag. `--config <path>` points either host elsewhere.

Language: `--language` only reaches two backends. `apple` runs it through `localeID()`, which needs
a real locale. `hf-stream` turns it into the checkpoint's language-ID prompt; the other workers
detect the language themselves and take no flag at all. On `--language auto` the Nemotron
checkpoint appends the locale it heard as a special token after each utterance's terminal
punctuation (`Hello.<en-US>`), so `hf_stream_worker.py` decodes with `skip_special_tokens=False`
and `partition_language()` splits it back out. `consume()` commits a line on punctuation, on a
tag, or on `SILENCE_SECONDS` of decoded audio with no new token, and holds a punctuated line for
`TAG_WAIT` so the tag — which arrives a step later — lands on the line it describes. Do not make
the tag the only commit point: the model punctuates far more often than it tags and does neither
while nobody talks, so captions then hang until the next speaker reaches a full stop. The silence
rule is what ends a half-finished sentence at a microphone/speaker handover; it is measured in
decoded audio because the streamer yields an empty string on every step that decoded nothing, so
silence looks like a stream of blanks rather than a pause, and a wall clock would cut sentences in
half on a slow device. `SILENCE_SECONDS` has to clear the model's lookahead — too short and the
last word, still in flight, is split into a line of its own.
Measured on a spoken English→Chinese→English clip: `auto` transcribes both and labels each line,
while pinning `--language zh-CN` loses the English passages entirely (and vice versa) — for a
bilingual meeting `auto` is not a refinement, it is the difference between having the text or not.

Device: both HF workers run `pick_device()` — cuda, then `mps` (Apple Silicon), then cpu. The two
copies are kept identical. `--device` pins one and `LIVECAPTION_DEVICE` overrides even that, which
is the only handle the hosts expose since they pass their environment straight to the worker.

## Conventions

- Python: 4-space indent, snake_case, short single-purpose modules with a usage docstring on top;
  hosts stick to stdlib + numpy, heavy ML deps are confined to the worker processes
- `# ponytail:` comments mark deliberate minimalism decisions — read them before "fixing" the code
- Docs come in bilingual pairs (README.md / README.zh.md, tutorial.md / tutorial.zh.md); keep both in sync
- ASR choices differ per host: Windows `--asr hf|hf-stream|sherpa|wsl-vllm`,
  macOS `--asr apple|hf|hf-stream|sherpa`. `--source mic|system|auto` is the same on both.

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
- `qwen-asr` pins `transformers==4.57.6` and `hf_stream_worker.py` needs `>= 5.13`, so one
  interpreter cannot serve both. `--hf-stream-python` (`HF_STREAM_PYTHON` for start.sh) points the
  streaming worker at a second environment; it defaults to `.build/stream-env` when
  setup-hf-stream.sh has built it, otherwise the host's own interpreter.
- `.build/stream-env` is a `--system-site-packages` venv, so it borrows torch from whichever
  `python3` created it. setup-hf-stream.sh rebuilds it only when that import breaks, so switching
  conda environments does not reinstall on every start — `rm -rf .build/stream-env` forces it.
- A 0.6B streaming checkpoint needs a GPU: on this project's Mac it runs 2.7x faster than realtime
  on `mps` and 3.3x slower than realtime on cpu, so a CPU fallback is not a slower fallback, it is
  a growing backlog that never catches up.
- `localeID()` is applied where `SFSpeechRecognizer` is built, not in `parseArgs` — it folds
  `auto` onto `zh-CN`, which is right for Apple Speech and wrong for every worker that can detect
  the language itself. `--language auto` and `--source auto` are unrelated.
- `Source` / the host's source strings mix two kinds of value: `mic` and `sys` are capture channels,
  `auto` is the one pane fed by both. Only channels get a WAV, a level meter of their own or a
  `-sys` transcript; `--record` / `--debug` stay per channel under `--source auto` because the
  gate picks captions, not recordings.
- `hf_stream_worker.py` gives each source a `copy.copy()` of the model: `generate()` patches
  `get_audio_features` onto the instance and deletes it on the way out, so two sources sharing one
  instance tear it out from under each other. The shallow copy shares the weight tensors.
- win_host.py shutdown deliberately calls `os._exit(0)` and skips `pa.terminate()` / stdin close —
  those can deadlock on live streams (see the comment at the end of `main()`).
- `config.json` is gitignored local config and may hold machine-specific paths (e.g. `wsl-python`);
  never commit it. `transcripts/` and `recordings/` hold private meeting data.
- Apple Speech realtime tasks rotate roughly every 50 s; the restart blip is expected, not a bug.
