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

Dual-source mode uses two independent recognition streams and two caption panes:

```text
┌─────────────────────────────┬─────────────────────────────┐
│ speaker / system            │ microphone                  │
│ Remote or computer playback │ What you say into the mic   │
└─────────────────────────────┴─────────────────────────────┘
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

- **Microphone**: required for `mic` or `both`
- **Screen Recording**: required for `system` or `both` (ScreenCaptureKit reads system audio this way)
- **Speech Recognition**: required only for `--asr apple`

The permission list may show Terminal, `live-subtitle`, or the terminal app that launched it. After changing permissions, stop LiveCaption and run the start command again; if needed, fully quit and reopen the terminal.

## 4. Recommended First Launch

Recognize meeting audio and your microphone together:

```bash
bash scripts/start.sh --source both --asr sherpa
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

Both:

```bash
bash scripts/start.sh --source both --asr sherpa
```

In `both` mode:

- Left pane `(speaker)` = system audio
- Right pane `(microphone)` = mic
- Both panes can update at once without overwriting each other
- Transcripts are saved separately

## 6. Chinese, English, and Mixed Input

Sherpa uses a bilingual model — no language flag needed:

```bash
bash scripts/start.sh --source both --asr sherpa
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

The window sits at the bottom of the screen:

- `Hide`: collapse to button height
- `Show`: restore full captions
- `Quit`: run the stop script and exit
- Select captions, then `Cmd+C`: copy selection
- `Cmd+C` with no selection: copy full history for the current pane
- Mouse wheel: scroll older captions

Adjust height and opacity:

```bash
bash scripts/start.sh \
  --source both \
  --asr sherpa \
  --height 160 \
  --opacity 0.85
```

Parameter changes need a stop + restart to take effect.

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
bash scripts/start.sh --source both --asr sherpa --debug
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
bash scripts/start.sh --source both --asr sherpa
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
bash scripts/start.sh --source both --asr sherpa
```

### No captions for system audio

1. Confirm the command uses `--source system` or `--source both`
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

When both work alone, use `--source both`. Sherpa creates an independent stream per source.

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
