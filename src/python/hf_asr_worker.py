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
    parser.add_argument("--chunk-seconds", type=float, default=3.0)
    return parser.parse_args()


def main():
    args = parse_args()

    try:
        import torch
        device = "cuda:0" if torch.cuda.is_available() else "cpu"
    except ImportError:
        torch, device = None, "cpu"
    print(f"ASR device: {device}" + ("" if device.startswith("cuda") else " (torch cannot see a GPU)"),
          file=sys.stderr)

    # ponytail: Qwen3-ASR needs its own package; everything else stays on the generic pipeline
    if "qwen3-asr" in args.hf_model.lower():
        try:
            from qwen_asr import Qwen3ASRModel
        except ImportError:
            print("Install qwen-asr to use Qwen3-ASR models: pip install qwen-asr", file=sys.stderr)
            return 1
        if device.startswith("cuda"):
            model = Qwen3ASRModel.from_pretrained(args.hf_model, dtype=torch.bfloat16, device_map=device)
        else:
            model = Qwen3ASRModel.from_pretrained(args.hf_model)

        def asr(inputs):
            results = model.transcribe(audio=(inputs["array"], inputs["sampling_rate"]))
            return {"text": results[0].text}
    else:
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
