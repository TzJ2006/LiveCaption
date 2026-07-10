#!/usr/bin/env python3
"""Hugging Face ASR worker using NDJSON over standard input and output.

Input:
  {"type":"audio","source":"mic","sampleRate":16000,"pcmFloat32":"..."}
Output:
  {"source":"mic","text":"hello","final":true}
"""

import argparse
import base64
import json
import sys
from collections import defaultdict

import numpy as np


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hf-model", required=True)
    parser.add_argument("--chunk-seconds", type=float, default=2.0)
    return parser.parse_args()


def main():
    args = parse_args()

    try:
        from transformers import pipeline
    except ImportError:
        print("Install transformers to use --asr hf: pip install transformers torch", file=sys.stderr)
        return 1

    asr = pipeline(
        "automatic-speech-recognition",
        model=args.hf_model,
        trust_remote_code=False,
    )
    buffers = defaultdict(list)
    sample_rates = {}

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
        if total / sample_rate < args.chunk_seconds:
            continue

        chunk = np.concatenate(buffers[source])
        buffers[source].clear()
        try:
            result = asr({"array": chunk, "sampling_rate": sample_rate})
            text = (result.get("text") or "").strip()
        except Exception as exc:
            print(f"asr error: {exc}", file=sys.stderr)
            continue
        if text:
            print(json.dumps({"source": source, "text": text, "final": True}, ensure_ascii=False), flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
