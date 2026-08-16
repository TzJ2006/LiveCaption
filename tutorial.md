# LiveCaption Tutorial

English | [中文](tutorial.zh.md)

**LiveCaption** is a lightweight multi-backend speech-to-text framework for macOS. This tutorial covers running it: system audio + microphone, English input, debug audio, and fixing “I hear sound but no captions.”

Meetings are a common use case (Zoom/Teams/Meet + your mic), but the same flow works for lectures, videos, or any live audio you want as text.

## 1. How It Works

LiveCaption’s Swift host:

1. Captures the microphone with `AVAudioEngine`
2. Captures current system output with `ScreenCaptureKit`
3. Sends audio to Apple Speech or local Sherpa-ONNX
4. Shows captions at the bottom of the screen
5. Writes final text under `transcripts/`

There is one caption pane. Under `--source auto` both channels are captured but only the one that
is talking is recognized, and each line says which channel it came from:

```text
┌───────────────────────────────────────────────────────────┐
│ (speaker) Remote or computer playback                     │
│ (microphone) What you say into the mic                    │
└───────────────────────────────────────────────────────────┘
```

## 2. Prepare the Environment

Enter the project directory:

```bash
cd /path/to/LiveCaption
```

Confirm basic tools:

```bash
sw_vers -productVersion
xcrun --find swiftc
python3 --version
```

If `xcrun --find swiftc` fails, install Xcode Command Line Tools:

```bash
xcode-select --install
```

## 3. Grant macOS Permissions

Open **System Settings → Privacy & Security** and enable what your source needs:

- **Microphone**: required for `mic` or `auto`
- **Screen Recording**: required for `system` or `auto` (ScreenCaptureKit reads system audio this way)
- **Speech Recognition**: required only for `--asr apple`

The permission list may show Terminal, `live-subtitle`, or the terminal app that launched it. After changing permissions, stop LiveCaption and run the start command again; if needed, fully quit and reopen the terminal.

## 4. Recommended First Launch

Recognize meeting audio and your microphone together:

```bash
bash scripts/start.sh --source auto --asr sherpa
```

If Sherpa is not installed locally, the start script will:

1. Install Python deps into `.build/pydeps/`
2. Put download caches and temp files under `.build/`
3. Install the bilingual INT8 model into `models/`
4. Load the model to verify it
5. Build and launch the caption window

If a download is interrupted, rerun the same command to continue. When finished, `models/` should contain:

```text
models/sherpa-onnx-streaming-paraformer-bilingual-zh-en/
├── encoder.int8.onnx
├── decoder.int8.onnx
└── tokens.txt
```

Or run setup alone:

```bash
bash scripts/setup-sherpa.sh
```

## 5. Choose an Audio Source

Microphone only:

```bash
bash scripts/start.sh --source mic --asr sherpa
```

Computer playback only:

```bash
bash scripts/start.sh --source system --asr sherpa
```

Both channels, one recognizer (the default):

```bash
bash scripts/start.sh --source auto --asr sherpa
```

In `auto` mode:

- Both channels are captured, but only the one that is talking is sent to the recognizer
- The speaker wins while it has voice; after ~0.6s of quiet the microphone takes over
- One pane, one recognizer — and every line is prefixed `(speaker)` or `(microphone)`
- Both channels share `transcripts/YYYY-MM-DD.txt`, with the same prefix written into each line
- A line is credited to whichever channel started it, so a handover mid-sentence does not split it
- `--record` / `--debug` still write one WAV per channel — the gate picks captions, not recordings

The old `--source both`, which ran a recognizer per channel in two side-by-side columns, is
retired. It is still accepted and resolves to `auto`.

## 6. Chinese, English, and Mixed Input

Sherpa uses a bilingual model — no language flag needed:

```bash
bash scripts/start.sh --source auto --asr sherpa
```

It handles Chinese, English, and mixed speech. Proper nouns, names, acronyms, and overlapping speakers can still be wrong.

Apple Speech Chinese:

```bash
bash scripts/start.sh --source mic --asr apple --language zh-CN
```

Apple Speech English:

```bash
bash scripts/start.sh --source mic --asr apple --language en-US
```

For dual-source meetings, prefer Sherpa so you are not limited by Apple Speech concurrent realtime tasks.

## 7. Using the Caption Window

The window starts at the bottom of the screen, spanning its full width:

- Drag handle (left of the model dropdown): move the window; it always keeps a corner reachable
- Model dropdown: switch the ASR model in place, without restarting
- `Hide`: collapse to a pill holding just the control bar, anchored at the bottom-right corner
- `Show`: restore full captions, growing back up and to the left from the pill
- `Quit`: stop LiveCaption and exit (macOS runs `scripts/stop.sh`)
- Select captions, then `Cmd+C` (Windows: `Ctrl+C`): copy selection
- Same shortcut with no selection: copy the full caption history
- Mouse wheel: scroll older captions

Adjust height and opacity:

```bash
bash scripts/start.sh \
  --source auto \
  --asr sherpa \
  --height 160 \
  --opacity 0.85
```

Parameter changes need a stop + restart to take effect — except the ASR model, which the dropdown
switches live.

Fill the dropdown with the Hugging Face models you want to reach:

```bash
bash scripts/start.sh \
  --source auto \
  --asr sherpa \
  --hf-models Qwen/Qwen3-ASR-0.6B,openai/whisper-large-v3-turbo
```

Picking an entry stops the running recognizer and starts the chosen one; capture and the
transcript files keep running, and the caption area reports `Switching to ...` then `... ready`.
If a model cannot start, the failure is reported there and the run continues — pick another entry.
Hugging Face weights download into `models/hf/` inside the project, so the first switch to a new
model takes as long as its download.

Each Hugging Face entry is tagged `(chunked)` or `(streaming)` — see the next section.

## 7a. Offline vs Streaming Hugging Face Models

`--asr hf` and `--asr hf-stream` are two different workers, because the models are two different
things:

- **`hf` (offline)** — Whisper, Qwen3-ASR and friends. The audio is cut into `--chunk-seconds`
  clips and each clip is transcribed on its own, so a caption appears only once its clip is over
  and every caption is final.
- **`hf-stream` (streaming)** — cache-aware RNNT checkpoints such as
  `nvidia/nemotron-3.5-asr-streaming-0.6b`. One recognition stays open for the whole run and is
  fed the chunk size the model was trained on, reusing its encoder cache. Text appears while the
  sentence is still being spoken and is committed when the model punctuates it.

```bash
bash scripts/start.sh \
  --source auto \
  --asr hf-stream \
  --hf-model nvidia/nemotron-3.5-asr-streaming-0.6b \
  --language auto
```

`--language` matters here: it becomes the model's language prompt. Use `auto` to let it detect
per utterance (useful when the speaker pane and your mic are in different languages), or a locale
such as `zh-CN` / `en-US` to pin it. Unsupported values fall back to `auto` with a note on stderr.

The dropdown decides which worker an id gets from the id itself — anything containing `streaming`
takes the streaming path. Override it with a prefix:

```bash
bash scripts/start.sh --source auto --asr sherpa \
  --hf-models stream:my/custom-cache-aware-model,offline:some/streaming-named-but-offline-model
```

Streaming checkpoints need `transformers >= 5.13`, while `qwen-asr` pins `transformers==4.57.6`,
so they cannot share one interpreter. `start.sh` handles this: the first time a streaming model is
in reach it runs `scripts/setup-hf-stream.sh`, which builds `.build/stream-env` inside the project
and installs the newer transformers there. Use `--hf-stream-python` if you keep your own instead.

### The GPU

Both Hugging Face workers pick a device by themselves — CUDA, then `mps` (Apple Silicon), then the
CPU. Check which one you got:

```bash
grep "ASR device" logs/subtitle.log
```

For the streaming path this decides whether the feature works at all. On this project's Mac, 44
seconds of audio through the 0.6B Nemotron model took **16 s on `mps`** (2.7x faster than
realtime) and **147 s on the CPU** (3.3x slower). Slower than realtime is not "laggy captions" —
the unprocessed audio piles up and the captions fall further behind every second, forever.

If an operation is missing on MPS in your torch build, force the CPU:

```bash
LIVECAPTION_DEVICE=cpu bash scripts/start.sh --source auto --asr hf-stream \
  --hf-model nvidia/nemotron-3.5-asr-streaming-0.6b
```

### If the dropdown looks empty

The menu lists the built-in backends plus **only the model ids you passed**. A bare
`bash scripts/start.sh` names no model, so nothing Hugging Face shows up. Either pass the ids, or
write them into `config.json` at the project root — both hosts read it now, and command-line flags
still override it:

```json
{
  "source": "auto",
  "asr": "hf-stream",
  "hf-model": "nvidia/nemotron-3.5-asr-streaming-0.6b",
  "hf-models": ["Qwen/Qwen3-ASR-0.6B"],
  "language": "auto"
}
```

## 8. Transcripts and Logs

Microphone final captions:

```text
transcripts/YYYY-MM-DD.txt
```

System-audio final captions:

```text
transcripts/YYYY-MM-DD-sys.txt
```

Runtime log:

```text
logs/subtitle.log
```

Stop log:

```text
logs/subtitle-stop.log
```

Recent log lines:

```bash
tail -n 100 logs/subtitle.log
```

All of these files stay inside the LiveCaption directory.

## 9. Debug Audio

If capture works but captions do not appear, use debug mode:

```bash
bash scripts/stop.sh
bash scripts/start.sh --source auto --asr sherpa --debug
```

The window shows dB levels for both inputs and writes under `debug-audio/`:

```text
YYYY-MM-DD-HHMMSS-microphone.wav
YYYY-MM-DD-HHMMSS-speaker.wav
```

How to read it:

- Level stuck on `waiting`: that source is not delivering audio frames
- dB moves but no captions: check ASR, language, or model logs
- WAV transcribes via file transcription: capture path is OK; focus on realtime ASR
- WAV is nearly silent: check input device, system volume, or permissions

## 10. Manually Transcribe a File

Apple Speech to the terminal:

```bash
bash scripts/transcribe.sh \
  "debug-audio/YYYY-MM-DD-HHMMSS-microphone.wav" \
  --language en-US
```

Save into the project:

```bash
bash scripts/transcribe.sh \
  "debug-audio/YYYY-MM-DD-HHMMSS-microphone.wav" \
  --language en-US \
  --output "transcripts/manual-transcription.txt"
```

Use `--language zh-CN` for Chinese files. Quote paths that contain spaces. The command waits until the whole file is processed, then exits.

## 11. Stop and Restart

Normal stop:

```bash
bash scripts/stop.sh
```

Then start again:

```bash
bash scripts/start.sh --source auto --asr sherpa
```

If you see `Subtitle window already running`, run the stop command first. The stop script also cleans up Sherpa / Hugging Face child processes.

## 12. Common Issues

### Caption window does not appear

Check the log:

```bash
tail -n 100 logs/subtitle.log
```

Then restart:

```bash
bash scripts/stop.sh
bash scripts/start.sh --source auto --asr sherpa
```

### No captions for system audio

1. Confirm the command uses `--source system` or `--source auto`
2. Confirm Screen Recording is enabled
3. Play audible content on the computer
4. Use `--debug` and check speaker dB
5. Restart after changing permissions

### No captions for the microphone

1. Confirm Microphone permission is enabled
2. Use `--debug` and check microphone dB
3. Inspect the generated `*-microphone.wav`
4. Run `transcribe.sh` on that file manually

### `No speech detected`

This is not always a timeout — it can mean low volume, noise, wrong language, or audio that is too short. Check the debug WAV first: if file transcription works but live captions are empty, the problem is more likely in the realtime ASR path.

### Sherpa model install failed

Check network and disk space, then rerun:

```bash
bash scripts/setup-sherpa.sh
```

Incomplete downloads stay in `models/*.part` and resume next time. Do not move models into your home directory; the app always reads from LiveCaption’s `models/`.

### Only one of two sources has content

Test separately:

```bash
bash scripts/start.sh --source mic --asr sherpa --debug
```

Stop, then:

```bash
bash scripts/start.sh --source system --asr sherpa --debug
```

When each works alone, use `--source auto`; the gate then picks between them.

## 13. Local Processing and Privacy

- After Sherpa models are installed, realtime recognition stays on-device
- Transcripts, logs, debug WAVs, models, and caches stay under LiveCaption
- Apple Speech may use Apple’s online speech services
- Hugging Face mode deps/caches are not managed by the auto-installer; use Sherpa if everything must stay in the project directory
- `src/python/query_transcript.py` sends transcript text to the API you configure (default: local Ollama)
- Confirm participant consent and follow local law and org policy before recording meetings

## Next Steps

- Overview: [README.md](README.md) · [中文](README.zh.md)
- Architecture notes for agents: [AGENTS.md](AGENTS.md)
