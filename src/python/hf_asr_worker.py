#!/usr/bin/env python3
"""Offline Hugging Face ASR worker using NDJSON over standard input and output.

Buffers --chunk-seconds of audio and runs each block through pipeline() as a self-contained clip,
so every caption is final. Cache-aware streaming checkpoints belong on hf_stream_worker.py, which
keeps one recognition alive across chunks instead.

Input:
  {"type":"audio","source":"mic","sampleRate":16000,"pcmFloat32":"..."}
Output:
  {"source":"mic","text":"hello","final":true}
"""

import argparse
import base64
import json
import os
import pathlib
import sys
from collections import defaultdict

# ponytail: keep downloaded weights inside LiveCaption (models/ is gitignored) instead of
# ~/.cache/huggingface. This has to run before transformers/huggingface_hub are imported --
# they read HF_HOME once, at import time. An HF_HOME already in the environment still wins.
HF_CACHE = pathlib.Path(__file__).resolve().parents[2] / "models" / "hf"
if not os.environ.get("HF_HOME"):
    os.environ["HF_HOME"] = str(HF_CACHE)
    HF_CACHE.mkdir(parents=True, exist_ok=True)

import numpy as np


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hf-model", required=True)
    parser.add_argument("--chunk-seconds", type=float, default=3.0)
    parser.add_argument("--device", default="auto",
                        help="auto picks cuda, then mps (Apple Silicon), then cpu; pass one to pin it")
    parser.add_argument("--context", default="",
                        help="free-form vocabulary hint for Qwen3-ASR (jargon, product names, "
                             "attendee names). It becomes the system message, so a plain "
                             "comma-separated term list works. Ignored by other models")
    return parser.parse_args()


def pick_device(preference):
    """CUDA, then the Apple Silicon GPU, then the CPU -- unless something pins one.

    LIVECAPTION_DEVICE wins over --device; the hosts pass the environment through, so it is the
    one way to force cpu from a start.sh command line.
    """
    try:
        import torch
    except ImportError:
        return "cpu"
    preference = os.environ.get("LIVECAPTION_DEVICE") or preference
    if preference != "auto":
        return preference
    if torch.cuda.is_available():
        return "cuda:0"
    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def main():
    args = parse_args()

    device = pick_device(args.device)
    print(f"ASR device: {device}" + (" (torch cannot see a GPU)" if device == "cpu" else ""),
          file=sys.stderr)
    print(f"HF cache: {os.environ['HF_HOME']}", file=sys.stderr)
    if "streaming" in args.hf_model.lower():
        print(f"note: {args.hf_model} looks like a cache-aware streaming checkpoint. This worker is"
              f" the offline path -- it cuts the audio into {args.chunk_seconds}s blocks and throws"
              " the encoder cache away between them. hf_stream_worker.py (--asr hf-stream) keeps it.",
              file=sys.stderr)

    # ponytail: Qwen3-ASR needs its own package; everything else stays on the generic pipeline
    if "qwen3-asr" in args.hf_model.lower():
        try:
            from qwen_asr import Qwen3ASRModel
        except ImportError:
            print("Install qwen-asr to use Qwen3-ASR models: pip install qwen-asr", file=sys.stderr)
            return 1
        kwargs = {} if device == "cpu" else {"device_map": device}
        if device.startswith("cuda"):
            import torch
            kwargs["dtype"] = torch.bfloat16
        try:
            model = Qwen3ASRModel.from_pretrained(args.hf_model, **kwargs)
        except (ValueError, RuntimeError, NotImplementedError) as exc:
            # ponytail: qwen-asr only promises cuda, so a device_map it does not know should cost
            # the acceleration, not the whole worker
            print(f"{device} rejected by qwen-asr ({exc}); using cpu", file=sys.stderr)
            device = "cpu"
            model = Qwen3ASRModel.from_pretrained(args.hf_model)

        def asr(inputs):
            # ponytail: context goes straight into the chat template's system message, so a bare
            # comma-separated term list is a valid value -- no formatting, no tokenizer work here.
            results = model.transcribe(audio=(inputs["array"], inputs["sampling_rate"]),
                                       context=args.context)
            return {"text": results[0].text}
    else:
        if args.context:
            print(f"note: --context only reaches Qwen3-ASR; {args.hf_model} ignores it",
                  file=sys.stderr)
        try:
            from transformers import pipeline
        except ImportError:
            print("Install transformers to use --asr hf: pip install transformers torch", file=sys.stderr)
            return 1

        asr = pipeline(
            "automatic-speech-recognition",
            model=args.hf_model,
            trust_remote_code=False,
            device=device,
        )
    print(json.dumps({"status": "ready", "device": device}), flush=True)
    buffers = defaultdict(list)
    sample_rates = {}

    def flush(source):
        """Recognize everything buffered for one source, as one self-contained clip.

        Called when the buffer reaches --chunk-seconds, and once per source at EOF -- without the
        EOF call the last partial chunk is silently dropped, which is the tail of whatever was
        being said when the host quit.
        """
        if not buffers[source]:
            return
        chunk = np.concatenate(buffers[source])
        buffers[source].clear()
        try:
            result = asr({"array": chunk, "sampling_rate": sample_rates[source]})
            text = (result.get("text") or "").strip()
        except Exception as exc:
            print(f"asr error: {exc}", file=sys.stderr)
            return
        if text:
            print(json.dumps({"source": source, "text": text, "final": True}, ensure_ascii=False),
                  flush=True)

    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if event.get("type") != "audio":
                continue
            source = event["source"]
            sample_rate = int(event["sampleRate"])
            audio = np.frombuffer(base64.b64decode(event["pcmFloat32"]), dtype=np.float32)
        except Exception as exc:
            print(f"bad input: {exc}", file=sys.stderr)
            continue

        if audio.size == 0:
            continue
        buffers[source].append(audio)
        sample_rates[source] = sample_rate

        total = sum(chunk.size for chunk in buffers[source])
        if total / sample_rate >= args.chunk_seconds:
            flush(source)

    for source in list(buffers):  # stdin closed: whatever is left is still speech
        flush(source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
