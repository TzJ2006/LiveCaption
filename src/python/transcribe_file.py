#!/usr/bin/env python3
"""Transcribe one audio file with any LiveCaption backend, on any platform.

The Windows/Linux answer to scripts/transcribe.sh, which is macOS-only (it compiles the Swift
Apple Speech host with xcrun). ffmpeg decodes whatever you have -- m4a, mp3, mp4, a raw WAV -- into
the 16 kHz mono PCM the workers expect, then the file is replayed through a real worker.

  python src/python/transcribe_file.py "recordings/New Recording 18.m4a"
  python src/python/transcribe_file.py meeting.m4a --asr sherpa \
      --model-dir models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17
  python src/python/transcribe_file.py meeting.m4a --asr api --hf-model gpt-4o-transcribe

Writes <input>.txt next to the input unless --output says otherwise.

Run it with an interpreter that has the backend's deps: --asr hf with a Qwen3-ASR model needs
qwen-asr and a CUDA torch, which on this machine is D:/Miniconda3/envs/AI/python.exe, not base.

# ponytail: no inference and no feeding loop here. bench_asr.run() already replays a clip through
# a worker spawned by win_host.worker_command(), so this is a decoder plus an output format --
# the file path and the live path stay one implementation, not two that drift.
"""

import argparse
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_asr  # noqa: E402
import win_host as host  # noqa: E402


def decode(path, out_wav):
    """Any container -> 16 kHz mono 16-bit WAV.

    Unconditional, even for a WAV input: a one-line ffmpeg call that also fixes rate and channel
    count is shorter than probing the header to decide whether to skip it.
    """
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", path,
                    "-ac", "1", "-ar", str(host.AudioGate.TARGET_RATE), "-c:a", "pcm_s16le",
                    out_wav], check=True)
    return out_wav


def main():
    p = argparse.ArgumentParser()
    p.add_argument("audio")
    p.add_argument("--output", default="", help="transcript path; default <input>.txt")
    p.add_argument("--asr", default="hf", choices=["hf", "hf-stream", "sherpa", "wsl-vllm", "api"])
    p.add_argument("--hf-model", default="Qwen/Qwen3-ASR-1.7B",
                   help="model id for --asr hf/hf-stream, or the hosted id for --asr api")
    p.add_argument("--chunk-seconds", type=float, default=30.0,
                   help="audio per call for the offline backends; a file has no latency budget, so "
                        "this is much larger than the live default of 3")
    p.add_argument("--context", default="", help="vocabulary hint for Qwen3-ASR")
    p.add_argument("--language", default="auto")
    p.add_argument("--model-dir", default="", help="model directory for --asr sherpa")
    p.add_argument("--key-config", default="", help="credentials file for --asr api")
    p.add_argument("--keep-wav", default="", help="also keep the decoded 16 kHz WAV here")
    p.add_argument("--hf-stream-python", default=host.stream_python())
    p.add_argument("--wsl-python", default="/home/tongt/miniconda3/envs/AI/bin/python")
    args = p.parse_args()

    # bench_asr.run() measures a live app; a file transcription wants none of that
    args.pace, args.power, args.warmup_seconds = "max", False, 0.0
    args.ready_timeout = args.drain_timeout = 3600.0

    label = args.hf_model or os.path.basename(args.model_dir.rstrip("/\\")) or args.asr
    choice = {"id": host.choice_id(args.asr, args.hf_model), "asr": args.asr,
              "hf_model": args.hf_model, "label": label, "menu": f"{label} ({args.asr})"}

    with tempfile.TemporaryDirectory() as tmp:
        wav = decode(args.audio, args.keep_wav or os.path.join(tmp, "clip.wav"))
        record = bench_asr.run(wav, choice, args)

    lines = [e["text"].strip() for e in record["events"] if e["final"] and e["text"].strip()]
    if not lines:
        print("no transcript: the worker produced nothing -- check its errors above "
              "(a missing dependency in this interpreter is the usual cause)", file=sys.stderr)
        return 1

    out = args.output or os.path.splitext(args.audio)[0] + ".txt"
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"{out}\n  {len(lines)} segments, {sum(len(l) for l in lines)} chars, "
          f"{record['audio_seconds'] / 60:.1f} min audio in {record['wall_seconds'] / 60:.1f} min "
          f"(RTF {record['rtf']:.3f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
